"""The FastAPI app: routes wired to real modules, not the request/response
shapes those modules had to guess at before they existed.

Runs on 127.0.0.1:8765, started by the app (or `python -m twomarkdown.server`
for development) and never exposed beyond localhost. See `docs/desktop-app.md`
for the endpoint list, the event schema, and how a preset's `Pipeline` maps
onto engine config; `twomarkdown/server/schemas.py` is the pydantic contract
this module fulfils.

Status: `system`, `presets`, `syncs`, `jobs` (including its WS event stream,
page read/write, and retry) and `inspect`/`estimate`/`prices/manual` (via
`twomarkdown.batch.estimate`, built alongside this module) are real. Nothing
here is a stub any more; where a corner was deliberately cut instead (job
pause/cancel granularity, sync watch limits), the module that made that call
documents it — `server/jobs.py` and `server/syncs.py`.
"""

from __future__ import annotations

import base64
import logging
import queue
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from twomarkdown.server import (
    folder_presets,
    host,
    jobs,
    models,
    presets,
    syncs,
    system,
)
from twomarkdown.server import (
    settings as settings_module,
)
from twomarkdown.server.schemas import (
    CloudKeyRequest,
    CloudKeyResponse,
    EstimateRequest,
    EstimateResponse,
    FolderPreset,
    InspectRequest,
    InspectResponse,
    Job,
    JobCreateRequest,
    JobCreateResponse,
    JobRetryRequest,
    ManualPriceRequest,
    ModelsResponse,
    OllamaPullRequest,
    OllamaPullResponse,
    OpenAIKeyRequest,
    OpenAIKeyResponse,
    OpenPathRequest,
    OpenPathResponse,
    OriginalInfo,
    PageDetail,
    PageUpdateRequest,
    PickFolderResponse,
    Preset,
    PresetsUpdateRequest,
    ResolveModelRequest,
    ResolveModelResponse,
    Settings,
    Sync,
    SyncCreateRequest,
    SyncUpdateRequest,
    SystemInfo,
)

logger = logging.getLogger(__name__)


# N10: a client with an open `WS /api/jobs/{id}/events` connection (or a UI
# tab left on the events stream of a job that already finished) is a
# perfectly normal thing to have around, and it must never be what makes
# `make app-down`/SIGTERM wait forever. `ws_job_events` below registers its
# own websocket here on accept and discards it in its `finally` — so this
# set only ever holds sockets a request handler is actively serving right
# now. `_lifespan`'s shutdown phase actively closes every one of them before
# `jobs.shutdown()` joins the job threads, instead of waiting for a client
# to notice the server is going away on its own (it might never — a
# disconnected laptop, a frozen tab).
_active_ws: set[WebSocket] = set()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    syncs.set_job_runner(_run_sync_job)
    syncs.start_all()
    yield
    # Close every open events WebSocket first — each one otherwise keeps a
    # request-handling task alive, and *that* is what previously kept the
    # whole process alive past uvicorn's own graceful-shutdown window (the
    # port closed, but the python process — and its `uv run` parent — sat in
    # state S forever; four orphan pairs had piled up before this). Code 1001
    # ("going away") is the correct close code for "the server is shutting
    # down", not an error on the client's part.
    for ws in list(_active_ws):
        try:
            await ws.close(code=1001, reason="server shutting down")
        except Exception:
            # Already closing/closed, or the client is gone — either way
            # there is nothing left to do for this one socket, and one bad
            # close must never stop the rest of shutdown.
            logger.debug(
                "Error closing a job-events WebSocket at shutdown", exc_info=True
            )
    _active_ws.clear()
    # Stop every sync watcher and join every job thread before the process
    # exits — a `watchfiles.watch` or `_run_job` thread still alive when the
    # interpreter starts tearing down can segfault instead of exiting
    # quietly (see INCONSISTENCIES.md); `make app`'s Ctrl-C should not risk
    # that.
    syncs.stop_all()
    jobs.shutdown()


app = FastAPI(title="2markdown desktop server", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inspections are cheap to redo and carry no state worth persisting, unlike
# jobs/presets/syncs — an in-memory cache keyed by `inspect_id` is enough so
# `/api/estimate` and `/api/jobs` don't re-walk the tree on every call.
_inspections: dict[str, InspectResponse] = {}


# ---------------------------------------------------------------------------
# GET /api/system
# ---------------------------------------------------------------------------


@app.get("/api/system")
def get_system() -> SystemInfo:
    return system.get_system_info()


# ---------------------------------------------------------------------------
# GET /api/models, POST /api/models/resolve
# ---------------------------------------------------------------------------


@app.get("/api/models")
def get_models() -> ModelsResponse:
    return models.get_models_info()


@app.post("/api/models/resolve")
def post_models_resolve(req: ResolveModelRequest) -> ResolveModelResponse:
    return models.resolve_model(req.id)


# ---------------------------------------------------------------------------
# POST /api/inspect, POST /api/estimate
# ---------------------------------------------------------------------------


@app.post("/api/inspect")
def post_inspect(req: InspectRequest) -> InspectResponse:
    from twomarkdown.batch.estimate import inspect

    try:
        response = inspect(req.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _inspections[response.inspect_id] = response
    return response


def _get_inspection(inspect_id: str) -> InspectResponse:
    inspection = _inspections.get(inspect_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="inspect_id not found")
    return inspection


@app.post("/api/estimate")
def post_estimate(req: EstimateRequest) -> EstimateResponse:
    from twomarkdown.batch.estimate import estimate, estimate_pipeline_only

    if req.inspect_id is None:
        # N1: no folder inspected (yet) — the Pipeline editor's own case.
        # `estimate_pipeline_only` still gives the real engine GPU verdict
        # for `req.pipeline`, just with zeroed-out stage seconds/usd (no
        # file data to size those from) and `estimate_scope:"pipeline_only"`.
        return estimate_pipeline_only(req.pipeline)

    inspection = _get_inspection(req.inspect_id)
    return estimate(inspection, req.pipeline)


# ---------------------------------------------------------------------------
# GET/PUT /api/presets
# ---------------------------------------------------------------------------


@app.get("/api/presets")
def get_presets() -> list[Preset]:
    return presets.list_presets()


@app.put("/api/presets")
def put_presets(req: PresetsUpdateRequest) -> list[Preset]:
    """FULL replace — see `presets.replace_all`'s docstring. A preset that
    exists but is not in `req.presets` is gone afterwards; prefer `PATCH
    /api/presets` to save/edit one preset without touching the rest."""
    return presets.replace_all(req.presets)


@app.patch("/api/presets")
def patch_presets(req: PresetsUpdateRequest) -> list[Preset]:
    """Merge/upsert by id — never deletes (see C7). This is what saving or
    editing a single preset should call: every preset already stored and
    not mentioned in `req.presets` survives untouched."""
    return presets.merge_upsert(req.presets)


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str) -> list[Preset]:
    """Explicit removal of one user preset. 404s for a builtin id (nothing
    to delete — reset it by PATCHing/PUTting its default pipeline back
    instead) or an id that was never a stored user preset."""
    if not presets.delete_preset(preset_id):
        raise HTTPException(status_code=404, detail=f"no such preset {preset_id!r}")
    return presets.list_presets()


# ---------------------------------------------------------------------------
# GET/PUT /api/settings — see server/settings.py's module docstring: the
# wizard is the first edit of this resource, not a separate in-memory store.
# ---------------------------------------------------------------------------


@app.get("/api/settings")
def get_settings() -> Settings:
    return settings_module.get_settings()


@app.put("/api/settings")
def put_settings(req: Settings) -> Settings:
    try:
        return settings_module.replace_settings(req)
    except settings_module.SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail()) from exc


# ---------------------------------------------------------------------------
# GET/DELETE /api/folders — "Equipo y sincronización"'s view of
# folder_presets.py; not the same resource as /api/syncs (see that module's
# own docstring on why).
# ---------------------------------------------------------------------------


@app.get("/api/folders")
def get_folders() -> list[FolderPreset]:
    return [FolderPreset(**row) for row in folder_presets.list_folders()]


@app.delete("/api/folders")
def delete_folder(path: str) -> dict[str, bool]:
    folder_presets.delete_folder(path)
    return {"ok": True}


def _reject_if_blocked(pipeline) -> None:
    """Refuse a pipeline `/api/estimate` would already show as blocked.

    The estimate endpoint computes this same condition (`batch.estimate.
    _gpu_state`'s resident-memory arithmetic — the pipeline's distinct local
    models, scaled to their resident GB, exceed what this machine's GPU has)
    purely to warn the UI; without this check the API itself would still
    accept and run a pipeline that does not fit, which only surfaces later as
    a run-time OOM/malformed-completion failure instead of a clean 422 up
    front.
    """
    from twomarkdown.batch.estimate import _blocked_info, _effective_figure_model

    review_model = pipeline.review_model or None
    figure_model = (
        _effective_figure_model(pipeline.ocr_model, pipeline.figure_model, review_model)
        if pipeline.describe_figures
        else None
    )
    blocked = _blocked_info(pipeline.ocr_model, figure_model, review_model)
    if blocked is not None:
        raise HTTPException(status_code=422, detail=f"{blocked.title}: {blocked.body}")


def _resolve_pipeline(req: JobCreateRequest):
    if req.pipeline is not None:
        return req.pipeline
    if req.preset_id is not None:
        preset = presets.get_preset(req.preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="preset_id not found")
        return preset.pipeline
    raise HTTPException(status_code=422, detail="pipeline or preset_id required")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@app.post("/api/jobs")
def post_jobs(req: JobCreateRequest) -> JobCreateResponse:
    if req.inspect_id is None and req.path is None:
        raise HTTPException(status_code=422, detail="inspect_id or path required")
    pipeline = _resolve_pipeline(req)
    _reject_if_blocked(pipeline)
    path = req.path
    if path is None and req.inspect_id is not None:
        # `InspectResponse.root` is the path the user gave `/api/inspect` (a
        # file's own path, or a walked directory's own path) — re-resolving a
        # job's files from it, single- or multi-file alike, is exactly what
        # `_resolve_files`/`normalize_batch_input` already do for an explicit
        # `path`, so a bare `inspect_id` reaches the same walk.
        inspection = _get_inspection(req.inspect_id)
        path = inspection.root
    output = req.output
    if output is None:
        # No explicit output — the same resolver `InspectResponse.
        # default_output` already showed the caller (server/settings.py),
        # so a caller that only sent `pipeline`/`preset_id` lands exactly
        # where Convertir's own preview said it would.
        if path is None:
            raise HTTPException(
                status_code=422, detail="output required when path is unknown"
            )
        output = settings_module.default_output_for(path)
    try:
        job = jobs.create_job(
            path=path,
            inspect_id=req.inspect_id,
            output=output,
            pipeline=pipeline,
            mode=req.mode,
            preset_id=req.preset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.mode == "once" and path is not None:
        # `req.preset_id` is `None` when the request sent a raw `Pipeline`
        # instead — recorded as such (see folder_presets.py's own docstring)
        # rather than skipped, so a folder that switches back and forth
        # between a preset and an ad-hoc pipeline still reflects the latest
        # choice, not a stale preset id.
        folder_presets.record(path, req.preset_id)
    return JobCreateResponse(job_id=job.job_id)


def _get_job(job_id: str) -> Job:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/api/jobs")
def get_jobs(limit: int | None = None) -> list[Job]:
    """Every job, newest first — in-memory ones plus whatever
    `jobs_history.json` still remembers past a server restart (see
    `server/jobs.py`'s history module docstring). The Historial screen's
    only endpoint; `GET /api/jobs/{id}` below still serves one job at a
    time, historical or not."""
    return jobs.list_jobs(limit)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    return _get_job(job_id)


@app.post("/api/jobs/{job_id}/pause")
def post_job_pause(job_id: str) -> Job:
    _get_job(job_id)
    return jobs.pause_job(job_id)


@app.post("/api/jobs/{job_id}/resume")
def post_job_resume(job_id: str) -> Job:
    _get_job(job_id)
    return jobs.resume_job(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def post_job_cancel(job_id: str) -> Job:
    _get_job(job_id)
    return jobs.cancel_job(job_id)


@app.post("/api/jobs/{job_id}/files/{file_index}/cancel")
def post_job_file_cancel(job_id: str, file_index: int) -> Job:
    """Stop one file without touching the rest of the job — queued: skipped
    outright; running: stopped after the current page. See `jobs.cancel_file`."""
    _get_job(job_id)
    try:
        return jobs.cancel_file(job_id, file_index)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="file_index out of range") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except jobs.FileNotCancellableError as exc:
        raise HTTPException(
            status_code=409, detail="file has already finished"
        ) from exc


@app.post("/api/jobs/{job_id}/retry")
def post_job_retry(job_id: str, req: JobRetryRequest) -> Job:
    _get_job(job_id)
    try:
        return jobs.retry(job_id, req)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="file_index out of range") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except jobs.UnsavedPageEditError as exc:
        # A saved page edit (`PUT .../pages/{n}`) sits on the page(s) this
        # retry would regenerate — refuse rather than silently drop it (see
        # N10). The caller re-sends the same body with `confirm: true` to
        # proceed anyway; Revisar should surface that choice to the user
        # rather than retrying with confirm=True on its own.
        #
        # `detail` is a dict, not the bare message: a plain string left the
        # app with nothing to switch on but English prose it had to print
        # verbatim (see N10 round 5 — "sin cambios" showed the raw
        # "POST /jobs/…/retry -> 409: {...}" text to a Spanish-speaking
        # user). `code` is the stable, English, never-translated field the
        # app matches on to show its own Spanish copy; `message` stays for
        # logs/debugging only, never for display.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "unsaved_page_edits",
                "message": str(exc),
                "file_index": exc.file_index,
                "pages": sorted(exc.pages),
            },
        ) from exc


@app.websocket("/api/jobs/{job_id}/events")
async def ws_job_events(websocket: WebSocket, job_id: str) -> None:
    subscription = jobs.subscribe(job_id)
    if subscription is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    _active_ws.add(websocket)
    buffered, q = subscription
    try:
        for event in buffered:
            await websocket.send_json(event)
        while True:
            # `queue.Queue.get` blocks a real thread, not the event loop, so
            # it runs off-loop — but a bare blocking `get()` would also block
            # a disconnect from ever being noticed (the thread pool call is
            # not cancellable mid-wait). A short timeout, retried, bounds how
            # long that takes without polling the queue itself.
            try:
                event = await run_in_threadpool(q.get, True, 1.0)
            except queue.Empty:
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug("Client disconnected from job %s events", job_id)
    except RuntimeError:
        # `_lifespan`'s shutdown already called `websocket.close()` on this
        # same connection from outside this loop (see `_active_ws` above) —
        # the next `send_json` here finds the socket already closed and
        # raises, same as a disconnect for this handler's purposes.
        logger.debug("Job %s events socket closed during shutdown", job_id)
    finally:
        _active_ws.discard(websocket)
        jobs.unsubscribe(job_id, q)


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/files/{i}/assets/{name}
#
# `{name:path}` (not `{name}`) so the route matches the multi-segment,
# percent-encoded-per-segment path Revisar's `assetSrc()` builds (see
# `app/src/screens/Review.tsx`) — e.g. `Tema 1_assets/Tema 1-fig-p1-1.png` —
# not just a single path segment.
# ---------------------------------------------------------------------------


@app.get("/api/jobs/{job_id}/files/{file_index}/assets/{name:path}")
def get_job_asset(job_id: str, file_index: int, name: str) -> FileResponse:
    _get_job(job_id)
    try:
        path = jobs.resolve_asset_path(job_id, file_index, name)
    except (KeyError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    return FileResponse(path)


# ---------------------------------------------------------------------------
# GET/PUT /api/jobs/{id}/files/{i}/pages/{n}
# ---------------------------------------------------------------------------


@app.get("/api/jobs/{job_id}/files/{file_index}/pages/{page}")
def get_job_page(job_id: str, file_index: int, page: int) -> PageDetail:
    """`pages` in the response is `None` for a file with no `## Page N`
    markers — its *whole* markdown is still returned in `markdown` (never a
    404) so Revisar can show any converted file, not only scanned PDFs (see
    docs/desktop-app.md's review-scope note)."""
    _get_job(job_id)
    try:
        png_bytes, markdown, page_count = jobs.read_page(job_id, file_index, page)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    image_b64 = base64.b64encode(png_bytes).decode("ascii") if png_bytes else None
    return PageDetail(
        image_png_base64=image_b64,
        image_url=None,
        markdown=markdown,
        changes=[],
        pages=page_count,
    )


@app.put("/api/jobs/{job_id}/files/{file_index}/pages/{page}")
def put_job_page(
    job_id: str, file_index: int, page: int, req: PageUpdateRequest
) -> PageDetail:
    _get_job(job_id)
    try:
        markdown = jobs.write_page(job_id, file_index, page, req.markdown)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PageDetail(
        image_png_base64=None, image_url=None, markdown=markdown, changes=[]
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/files/{i}/original — "ver original"
# ---------------------------------------------------------------------------


@app.get("/api/jobs/{job_id}/files/{file_index}/original")
def get_job_original(job_id: str, file_index: int) -> OriginalInfo:
    _get_job(job_id)
    try:
        kind, source, pages = jobs.get_original_info(job_id, file_index)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="file_index out of range") from exc
    if kind == "image":
        if not source.is_file():
            raise HTTPException(status_code=404, detail="original file not found")
        return FileResponse(source)  # type: ignore[return-value]
    if kind == "pdf":
        return OriginalInfo(kind="pdf", previewable=True, pages=pages)
    return OriginalInfo(kind=kind, previewable=False, path=str(source))


# ---------------------------------------------------------------------------
# POST /api/open, POST /api/reveal — open/reveal a path with the OS default
# app (macOS `open` / `open -R`), guarded to a job's own input/output roots
# or the user's home (see server/jobs.py:is_path_allowed).
# ---------------------------------------------------------------------------


def _reject_if_path_not_allowed(path: str) -> None:
    """403, not a 200 `{ok: false}` — a path outside every known job's
    input/output root and the user's own home is a request this server
    should refuse outright, not merely report failing (see
    `jobs.is_path_allowed`). `jobs.open_path` re-checks the same thing on its
    own (it has no other caller, and must stay safe called directly too), so
    this duplicates the check rather than trusting the route to always run
    first — but only this route decides the *status code* a disallowed path
    gets back."""
    candidate = Path(path).expanduser()
    if not jobs.is_path_allowed(candidate):
        raise HTTPException(
            status_code=403,
            detail="path is outside the allowed job/output/home roots",
        )


@app.post("/api/open")
async def post_open(req: OpenPathRequest) -> OpenPathResponse:
    _reject_if_path_not_allowed(req.path)
    error = await run_in_threadpool(jobs.open_path, req.path)
    return OpenPathResponse(ok=error is None, error=error)


@app.post("/api/reveal")
async def post_reveal(req: OpenPathRequest) -> OpenPathResponse:
    _reject_if_path_not_allowed(req.path)
    error = await run_in_threadpool(jobs.open_path, req.path, reveal=True)
    return OpenPathResponse(ok=error is None, error=error)


# ---------------------------------------------------------------------------
# GET/POST/DELETE /api/syncs
# ---------------------------------------------------------------------------


def _run_sync_job(sync: Sync) -> None:
    """`syncs.py`'s job runner — kept here, not in `syncs.py`, so a watched
    folder starting a conversion goes through the exact same `jobs.create_job`
    every other job does (preset resolution included)."""
    preset = presets.get_preset(sync.preset_id)
    if preset is None:
        logger.warning("Sync %s: preset %s no longer exists", sync.id, sync.preset_id)
        return
    jobs.create_job(
        path=sync.input,
        inspect_id=None,
        output=sync.output,
        pipeline=preset.pipeline,
        mode="once",
    )


@app.get("/api/syncs")
def get_syncs() -> list[Sync]:
    return syncs.list_syncs()


@app.post("/api/syncs")
def post_syncs(req: SyncCreateRequest) -> Sync:
    if presets.get_preset(req.preset_id) is None:
        raise HTTPException(status_code=404, detail="preset_id not found")
    return syncs.create_sync(req)


@app.patch("/api/syncs/{sync_id}")
def patch_syncs(sync_id: str, req: SyncUpdateRequest) -> Sync:
    sync = syncs.update_sync(sync_id, req)
    if sync is None:
        raise HTTPException(status_code=404, detail="sync not found")
    return sync


@app.delete("/api/syncs/{sync_id}")
def delete_syncs(sync_id: str) -> dict[str, bool]:
    if not syncs.delete_sync(sync_id):
        raise HTTPException(status_code=404, detail="sync not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/prices/manual
# ---------------------------------------------------------------------------


@app.post("/api/prices/manual")
def post_prices_manual(req: ManualPriceRequest) -> ManualPriceRequest:
    from twomarkdown.batch.estimate import save_manual_price

    save_manual_price(req.model, req.input_per_mtok, req.output_per_mtok)
    return req


# ---------------------------------------------------------------------------
# POST /api/pick-folder, POST /api/ollama/pull, POST /api/cloud/openai/key
#
# Not in docs/desktop-app.md's table — see app/src/api/contract.ts and
# twomarkdown/server/host.py.
# ---------------------------------------------------------------------------


@app.post("/api/pick-folder")
async def post_pick_folder() -> PickFolderResponse:
    path = await run_in_threadpool(host.pick_folder)
    return PickFolderResponse(path=path)


@app.post("/api/ollama/pull")
async def post_ollama_pull(req: OllamaPullRequest) -> OllamaPullResponse:
    ok, error = await run_in_threadpool(host.pull_ollama_model, req.model)
    return OllamaPullResponse(ok=ok, error=error)


@app.post("/api/cloud/openai/key")
async def post_openai_key(req: OpenAIKeyRequest) -> OpenAIKeyResponse:
    """Kept working as a plain alias for `POST /api/cloud/openai/key` below
    (declared first so this exact path wins over the `{provider}` pattern) —
    existing callers of this specific endpoint see no change."""
    ok = await run_in_threadpool(host.save_openai_key, req.key)
    return OpenAIKeyResponse(ok=ok)


# ---------------------------------------------------------------------------
# POST/DELETE /api/cloud/{provider}/key — every provider in
# `server/models.py:CLOUD_PROVIDER_ENV_VARS` (openai included; the endpoint
# above stays as a working alias for it). The env var a provider's key lives
# under is a backend-only detail (`server/host.py`) — the request/response
# only ever carry the provider id, `key_present`, and a best-effort `status`.
# ---------------------------------------------------------------------------


@app.post("/api/cloud/{provider}/key")
async def post_cloud_key(provider: str, req: CloudKeyRequest) -> CloudKeyResponse:
    if provider not in models.CLOUD_PROVIDER_ENV_VARS:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    ok = await run_in_threadpool(host.save_cloud_key, provider, req.key)
    if not ok:
        raise HTTPException(status_code=500, detail="could not save key")
    status = await run_in_threadpool(host.probe_cloud_key, provider, req.key)
    # C6: this response is not the only place that verdict must show up —
    # `GET /api/system` (a plain page reload, most obviously) recomputes
    # each provider's entry independently via `system.provider_cloud_info`,
    # which otherwise has no way to know a key was just validated and would
    # fall back to a guessed "unknown". Cache it explicitly here rather than
    # only inside `host.probe_cloud_key` so this still holds even when a
    # test (or a future caller) mocks that function out.
    system.cache_probe_verdict(provider, status)
    return CloudKeyResponse(provider=provider, key_present=True, status=status)


@app.delete("/api/cloud/{provider}/key")
async def delete_cloud_key(provider: str) -> CloudKeyResponse:
    if provider not in models.CLOUD_PROVIDER_ENV_VARS:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    ok = await run_in_threadpool(host.delete_cloud_key, provider)
    if not ok:
        raise HTTPException(status_code=500, detail="could not remove key")
    return CloudKeyResponse(provider=provider, key_present=False, status="no_key")
