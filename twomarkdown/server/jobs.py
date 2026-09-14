"""Run conversions on a background thread and stream their events.

A job is the existing `twomarkdown.batch.processor.process_batch()` entry
point, wired to a `Pipeline` (see docs/desktop-app.md, "Presets → engine
config") and driven from a background thread so `POST /api/jobs` can return
immediately. Progress reaches the WebSocket through `twomarkdown.batch.events`
— each job attaches its own sink for the duration of its run and detaches it
when done, so only one job's engine config and event stream are ever live at
once (`_ENGINE_LOCK` below enforces that; see its docstring for why).

**Pause/cancel is checked between files, not mid-file** (the file in flight
finishes — a "soft" stop, same spirit as the file-level timeout cancellation
already inside `process_batch`). This module gets that by calling
`process_batch(..., only_files=[one_file])` once per file from its own loop,
rather than one call over the whole job: `process_batch`'s own scheduling
loop has no hook for an external pause and this task's scope does not extend
to adding one — see `_run_job` below. Two things this trades away, on
purpose, for that control point: the planner's cross-file cost-based run
order (each file is planned in isolation) and, for a directory input, ZIP
auto-explosion (only a single file or a plain directory of ordinary files is
supported — no `.zip` members) — a real gap, not a hidden one; `only_files`
resolution below documents it again where it applies.

Everything here is in-memory, like `server/__init__.py`'s existing stores: a
restart loses running/queued jobs. Fine for a local single-user server; the
job list was never meant to survive past the app that started it.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz

from twomarkdown.batch import events, gpu_memory
from twomarkdown.batch.processor import plan_output_paths, process_batch
from twomarkdown.batch.walker import discover_files
from twomarkdown.config import (
    DEFAULT_INCLUDE_EXTENSIONS,
    IWORK_BUNDLE_SUFFIXES,
    conversion_config,
    figure_config,
    llm_config,
    pdf_ocr_config,
)
from twomarkdown.paths import normalize_batch_input
from twomarkdown.server.schemas import (
    Job,
    JobFileKind,
    JobFileState,
    JobRetryRequest,
    Pipeline,
)

logger = logging.getLogger(__name__)

# Same on-disk convention as presets.py/settings.py/folder_presets.py (one
# small JSON file per resource, all in the same folder) — see `_persist_history`
# below for why a *finished* job gets one of its own instead of staying
# in-memory like a running one.
HISTORY_DIR = Path.home() / "Library" / "Application Support" / "2markdown"
HISTORY_FILE = HISTORY_DIR / "jobs_history.json"

# ---------------------------------------------------------------------------
# Job.files[].kind — the source format, guessed from the suffix alone (cheap,
# no file open) at job creation. Deliberately coarser-grained than nothing:
# good enough to pick an icon and decide "does 'pages' mean anything for this
# row" without waiting for the engine to touch the file at all.
# ---------------------------------------------------------------------------

_IMAGE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".tif",
        ".bmp",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".svg",
    }
)
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_EBOOK_SUFFIXES = frozenset({".epub", ".mobi", ".azw", ".azw3", ".fb2"})
_MINDMAP_SUFFIXES = frozenset({".xmind"})
_OFFICE_SUFFIXES = (
    frozenset(
        {
            ".docx",
            ".pptx",
            ".xlsx",
            ".xlsm",
            ".xls",
            ".odt",
            ".ods",
            ".odp",
            ".rtf",
            ".doc",
            ".ppt",
        }
    )
    | IWORK_BUNDLE_SUFFIXES
)


def _classify_file_kind(path: Path) -> JobFileKind:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _HTML_SUFFIXES:
        return "html"
    if suffix in _EBOOK_SUFFIXES:
        return "ebook"
    if suffix in _MINDMAP_SUFFIXES:
        return "mindmap"
    if suffix in _OFFICE_SUFFIXES:
        return "office"
    if suffix in DEFAULT_INCLUDE_EXTENSIONS:
        return "text"
    return "other"


# One job's engine config (conversion_config/llm_config/figure_config/
# pdf_ocr_config — all process-wide singletons) and one job's event sink are
# ever active together. `process_batch` already makes this same assumption for
# its own globals (telemetry's `_batch`, `batch/events.py`'s sink slot); a
# desktop app runs one conversion at a time, so this is a real constraint of
# the engine, not a corner cut for this server.
_ENGINE_LOCK = threading.RLock()


@dataclass
class _Runtime:
    job: Job
    input_root: Path
    files: list[Path]
    event_log: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    usd_so_far: float = 0.0
    # `{file_index: {page numbers}}` a user has saved an edit to (via `PUT
    # .../pages/{n}`) since that page was last converted — `write_page` adds
    # to this, `retry` consults it (see N10) and clears it for whatever it
    # just re-read. A restart loses it same as everything else in `_Runtime`;
    # fine here too, since a restart also loses the running job it would be
    # protecting mid-review.
    edited_pages: dict[int, set[int]] = field(default_factory=dict)
    # The background thread running `_run_job` for this job — kept so
    # `shutdown()` can cancel and join it instead of leaving it running past
    # the server process (or a test) that started it. A daemon thread still
    # inside `process_batch` when the interpreter starts tearing down can
    # segfault rather than exit quietly (see INCONSISTENCIES.md).
    thread: threading.Thread | None = None
    # `Job.files[]` index this job's loop is converting right now, or `None`
    # between files — read by `cancel_file()` to tell "queued" (skip it, no
    # output) apart from "running" (stop it after the current page) and by
    # `cancel_job()` to reach that one file's own cancel token immediately
    # instead of waiting for the between-files check.
    current_index: int | None = None
    # This file's own stop token for the run in progress — a fresh
    # `threading.Event()` per file (not `cancel_event`, which is job-wide and
    # would wrongly also stop every *later* file once set). Threaded into
    # `process_batch(cancel=...)`, which forwards it into
    # `converter/pdf_ocr.py`'s per-page loop — see that module's docstring.
    file_cancel_events: dict[int, threading.Event] = field(default_factory=dict)
    # Which `file_cancel_events` were set by an explicit *per-file* cancel
    # (`cancel_file()`) rather than a job-wide one — `_run_job` uses this to
    # label that file "cancelled" rather than "failed" once `process_batch`
    # returns/raises for it, without also mislabeling an unrelated per-file
    # timeout (which sets the same kind of token, but never adds here) as a
    # cancel.
    file_cancel_requested: set[int] = field(default_factory=set)
    # Last OCR engine reported for each file's pages (`page_done`'s `engine`),
    # and how many `review_changes` its pages carried in total — both reset
    # before a (re)run of that file so a retry's own facts never carry over
    # a previous attempt's. Read into `JobFileState.engine_used`/
    # `.review_changes_count` at `file_done`.
    file_engine: dict[int, str] = field(default_factory=dict)
    file_review_counts: dict[int, int] = field(default_factory=dict)
    # This job's own id → output path map, computed once up front from the
    # same `plan_output_paths()` `process_batch` itself uses — so
    # `JobFileState.output_path` (needed by the queue-as-file-list UI to open/
    # reveal a finished file) is known immediately at job creation, not only
    # once that file has actually converted.
    planned_outputs: dict[Path, Path] = field(default_factory=dict)


_jobs: dict[str, _Runtime] = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# File discovery — the part `process_batch` normally does for a whole tree at
# once, done up front here so the per-file loop below has a fixed `files`
# list (and so `index` in every event matches this job's `Job.files`).
# ---------------------------------------------------------------------------


def _resolve_files(path: str, output: Path) -> tuple[Path, list[Path]]:
    """(input_root, files) for a job's `path`.

    A single file converts just that file. A directory is walked with
    `batch.walker.discover_files` — the same walk `process_batch` would do,
    minus ZIP auto-explosion and iWork PDF pre-export, both of which need
    the full `process_batch` call to happen instead (see module docstring).
    """
    batch_root, _default_out, only_files = normalize_batch_input(Path(path))
    if only_files is not None:
        return batch_root, only_files
    files = discover_files(batch_root, output)
    return batch_root, files


def _resolve_job_input(
    *, path: str | None, inspect_id: str | None, output: Path
) -> tuple[Path, list[Path]]:
    if path is not None:
        return _resolve_files(path, output)
    # `server/app.py`'s `POST /api/jobs` always resolves an `inspect_id`-only
    # request into `path` first — `InspectResponse.root`, the walked root
    # `twomarkdown.batch.estimate.inspect()` recorded — before calling
    # `create_job`, so this is only reached by a caller that skips that step.
    raise ValueError(f"inspect_id {inspect_id!r} needs `path` (no root resolved)")


# ---------------------------------------------------------------------------
# Pipeline -> engine config (docs/desktop-app.md, "Presets → engine config")
# ---------------------------------------------------------------------------


def _apply_pipeline(pipeline: Pipeline) -> dict[str, Any]:
    """Push `pipeline` onto the engine's global config; return what it was."""
    from twomarkdown.agents.image_ocr import normalize_model_id

    snapshot = {
        "ocr_enabled": conversion_config.ocr_enabled,
        "ocr_backend": conversion_config.ocr_backend,
        "parallel_workers": conversion_config.parallel_workers,
        "clean_markdown": conversion_config.clean_markdown,
        "extract_tables": conversion_config.extract_tables,
        "describe_figures": conversion_config.describe_figures,
        "emit_chunks": conversion_config.emit_chunks,
        "llm_enabled": llm_config.llm_enabled,
        "vision_model": llm_config.vision_model,
        "figure_model": llm_config.figure_model,
        "review_model": llm_config.review_model,
        "describe_figures_llm": figure_config.describe_figures_llm,
        "pdf_ocr_dpi": pdf_ocr_config.pdf_ocr_dpi,
    }

    conversion_config.ocr_enabled = True
    if pipeline.ocr_model == "tesseract":
        conversion_config.ocr_backend = "tesseract"
    else:
        conversion_config.ocr_backend = "ollama"
        llm_config.vision_model = normalize_model_id(pipeline.ocr_model)
    conversion_config.parallel_workers = max(1, pipeline.workers)
    conversion_config.clean_markdown = pipeline.clean
    conversion_config.extract_tables = pipeline.tables
    conversion_config.emit_chunks = pipeline.emit_chunks
    conversion_config.describe_figures = pipeline.describe_figures
    figure_config.describe_figures_llm = pipeline.describe_figures
    if pipeline.figure_model:
        llm_config.figure_model = normalize_model_id(pipeline.figure_model)
    llm_config.review_model = (
        normalize_model_id(pipeline.review_model) if pipeline.review_model else ""
    )
    # `llm_enabled` gates every LLM-backed pass (OCR, figures, review — see
    # `agents/image_ocr.py` and `agents/page_review.py`'s `review_enabled()`),
    # not just OCR: it used to be `pipeline.ocr_model != "tesseract"` alone,
    # so a pipeline reading OCR with plain Tesseract but reviewing pages with
    # a model, or captioning figures with one, silently ran neither (see M6:
    # a retry that only patched `review_model` was a no-op for exactly this
    # reason — the OCR model it inherited was "tesseract").
    llm_config.llm_enabled = (
        pipeline.ocr_model != "tesseract"
        or bool(pipeline.review_model)
        or (pipeline.describe_figures and bool(pipeline.figure_model))
    )
    pdf_ocr_config.pdf_ocr_dpi = pipeline.ocr_dpi
    return snapshot


def _warn_if_figure_model_collapses(pipeline: Pipeline) -> None:
    """Log `gpu_memory.effective_figure_model`'s collapse message once, at
    job start, when this pipeline's figure model will not actually run.

    `agents.image_ocr.effective_figure_model()` asks the same question again
    for every figure it captions (a job can have many), which would log the
    same sentence once per figure — correct but noisy, and easy to miss
    among the rest of a long run's log. Config is already applied
    (`_apply_pipeline` above), so this reads the exact models/limits the job
    is about to run with and reports the substitution exactly once, before
    the first file even starts.
    """
    if not pipeline.describe_figures or not pipeline.figure_model:
        return
    review_model = pipeline.review_model or None
    _, message = gpu_memory.effective_figure_model(
        llm_config.vision_model, llm_config.figure_model, review_model
    )
    if message:
        logger.warning(message)
        events.log("warning", message)


def _restore_config(snapshot: dict[str, Any]) -> None:
    conversion_config.ocr_enabled = snapshot["ocr_enabled"]
    conversion_config.ocr_backend = snapshot["ocr_backend"]
    conversion_config.parallel_workers = snapshot["parallel_workers"]
    conversion_config.clean_markdown = snapshot["clean_markdown"]
    conversion_config.extract_tables = snapshot["extract_tables"]
    conversion_config.describe_figures = snapshot["describe_figures"]
    conversion_config.emit_chunks = snapshot["emit_chunks"]
    llm_config.llm_enabled = snapshot["llm_enabled"]
    llm_config.vision_model = snapshot["vision_model"]
    llm_config.figure_model = snapshot["figure_model"]
    llm_config.review_model = snapshot["review_model"]
    figure_config.describe_figures_llm = snapshot["describe_figures_llm"]
    pdf_ocr_config.pdf_ocr_dpi = snapshot["pdf_ocr_dpi"]


# ---------------------------------------------------------------------------
# Event sink: buffer + fan out to every connected WebSocket
# ---------------------------------------------------------------------------


def _make_sink(rt: _Runtime):
    def _sink(event: dict[str, Any]) -> None:
        with rt.lock:
            rt.event_log.append(event)
            subscribers = list(rt.subscribers)
        if event.get("type") == "cost":
            rt.usd_so_far = float(event.get("usd_so_far", rt.usd_so_far))
        elif event.get("type") == "file_started":
            # The engine only knows a file's real page count once it opens it
            # (batch.planner's PyMuPDF pass) — Job.files[] is built before that,
            # from a bare file list, so this is the only point that can carry it
            # into the job the API/UI reads. Without it Revisar's page list
            # (built from `f.pages`) stays empty forever (see M3). Only ever
            # set for `kind == "pdf"` — every other kind's `pages` stays
            # `None` forever, never a bogus `0` (see `JobFileState.pages`'s
            # own docstring).
            index = event.get("index")
            if (
                isinstance(index, int)
                and 0 <= index < len(rt.job.files)
                and rt.job.files[index].kind == "pdf"
            ):
                rt.job.files[index].pages = int(event.get("pages", 0) or 0)
        elif event.get("type") == "page_started":
            # N10: `events.page_started()` (twomarkdown/batch/events.py, out
            # of this module's own ownership) has no idea which model this
            # job is running — only this sink, which already knows
            # `rt.job.pipeline`, can fill it in. Stamped onto the same dict
            # object already appended to `rt.event_log` above, so a replayed
            # buffer and a live subscriber both see it. Never overwrites an
            # `engine` a future emitter sets on its own.
            if "engine" not in event:
                event["engine"] = rt.job.pipeline.ocr_model
        elif event.get("type") == "page_done":
            index = event.get("index")
            if isinstance(index, int):
                engine = event.get("engine")
                if engine:
                    rt.file_engine[index] = str(engine)
                changes = event.get("review_changes") or []
                rt.file_review_counts[index] = rt.file_review_counts.get(
                    index, 0
                ) + len(changes)
        elif event.get("type") == "file_done":
            # `_run_job` (and the full-file branch of `retry`) also assign
            # `.status`/`.seconds` themselves from the `BatchResult` they get
            # back — redundant with what's applied here, but consistent,
            # since both read the same run. The single-page retry path
            # (`_retry_single_page`) has no `BatchResult` of its own, so this
            # sink is the *only* place its outcome ever reaches `Job.files[]`;
            # without it, a page-level retry's own status/reason/seconds were
            # simply never written, leaving whatever a previous attempt left
            # behind (see M6) — including a `.reason` naming a model or a
            # `.seconds` timing that belongs to that superseded attempt.
            index = event.get("index")
            if isinstance(index, int) and 0 <= index < len(rt.job.files):
                status = event.get("status")
                if status:
                    rt.job.files[index].status = status
                reason = event.get("reason")
                if reason:
                    rt.job.files[index].reason = reason
                elif status == "ok":
                    rt.job.files[index].reason = None
                seconds = event.get("seconds")
                if seconds is not None:
                    rt.job.files[index].seconds = round(float(seconds), 2)
        for q in subscribers:
            q.put(event)

    return _sink


def _publish(rt: _Runtime, event: dict[str, Any]) -> None:
    """Emit an event this module builds itself (job_started/job_done) — the
    engine-level ones go through `batch.events` instead, same as any other
    consumer of that sink."""
    _make_sink(rt)(event)


def _publish_cancelled_skip(rt: _Runtime, index: int, reason: str) -> None:
    """`file_done{status:"cancelled"}` for a file that never actually ran —
    skipped outright while still queued, by a job-wide cancel (`cancel_job()`,
    `_run_job`'s between-files check) or a per-file one (`cancel_file()`).
    `process_batch` never touches this file (see those callers' own
    docstrings), so nothing else would ever emit its `file_done` — without
    this, a queued file cancelled before it started left the WS event stream
    silent about it even though `Job.files[index].status` (and the queue row
    reading it) already say "cancelled".
    """
    _publish(
        rt,
        {
            "type": "file_done",
            "index": index,
            "status": "cancelled",
            "seconds": 0.0,
            "reason": reason,
        },
    )


class _EngineLogHandler(logging.Handler):
    """Forwards `twomarkdown.*` log records into the job's event stream.

    `events.log(...)` (the WS `log` event Revisar's "Detalles técnicos" is
    meant to show) exists but nothing ever called it (see M4) — every real
    failure only ever reached the server's own stderr. Attached to the
    `twomarkdown` logger (the common ancestor of every module logger here)
    for exactly the span one job runs, same lifetime as the sink itself.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # `record.created` is when the line actually happened, not when a
            # WebSocket client later replays it — see `events.log`'s `ts` and
            # N2 (a reconnect used to re-stamp the whole backlog at once).
            ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
            events.log(record.levelname.lower(), self.format(record), ts=ts)
        except Exception:
            pass  # a logging problem must never cost the batch a file


_ENGINE_LOGGER = logging.getLogger("twomarkdown")


# ---------------------------------------------------------------------------
# Running the job
# ---------------------------------------------------------------------------


def _fill_file_facts(rt: _Runtime, index: int) -> None:
    """After a file has run (any outcome), refresh the facts a queue row
    needs beyond bare status — `output_path`/`assets_dir` (only if something
    was actually written), `engine_used`, `review_changes_count`. Called from
    both `_run_job`'s per-file loop and `retry()`, so a retried file's facts
    are exactly as fresh as a first-run file's.
    """
    file_state = rt.job.files[index]
    source = rt.files[index] if 0 <= index < len(rt.files) else None
    output_path = _output_path_for(rt, index) if source is not None else None
    if output_path is not None and output_path.exists():
        file_state.output_path = str(output_path)
        assets_dir = output_path.parent / f"{output_path.stem}_assets"
        file_state.assets_dir = str(assets_dir) if assets_dir.is_dir() else None
    engine = rt.file_engine.get(index)
    if engine:
        file_state.engine_used = engine
    elif file_state.kind in ("pdf", "image"):
        # No per-page engine event fired at all (a digital PDF that needed no
        # OCR, or OCR disabled outright) — "none" is still a real answer, not
        # a gap, for a kind OCR *could* have touched.
        file_state.engine_used = (
            rt.job.pipeline.ocr_model if conversion_config.ocr_enabled else "none"
        )
    else:
        file_state.engine_used = "none"
    file_state.review_changes_count = rt.file_review_counts.get(index, 0)


def _run_job(job_id: str) -> None:
    rt = _jobs[job_id]
    job = rt.job
    job.status = "running"
    job.started = datetime.now(UTC).isoformat()
    log_handler = _EngineLogHandler(level=logging.INFO)
    with _ENGINE_LOCK:
        events.set_sink(_make_sink(rt))
        _ENGINE_LOGGER.addHandler(log_handler)
        snapshot = _apply_pipeline(job.pipeline)
        _warn_if_figure_model_collapses(job.pipeline)
        _publish(rt, {"type": "job_started", "job_id": job_id})
        started = time.perf_counter()
        try:
            for index, path in enumerate(rt.files):
                # A per-file cancel (`cancel_file()`) already marked this file
                # "cancelled" while it was still queued — skip it outright,
                # no attempt, no output (see that function's own docstring).
                if job.files[index].status == "cancelled":
                    continue
                if rt.cancel_event.is_set():
                    job.files[index].status = "cancelled"
                    job.files[index].reason = "cancelado antes de empezar"
                    job.cancelled += 1
                    _publish_cancelled_skip(rt, index, "cancelado antes de empezar")
                    continue
                while rt.pause_event.is_set():
                    if rt.cancel_event.is_set():
                        break
                    time.sleep(0.2)
                if rt.cancel_event.is_set():
                    job.files[index].status = "cancelled"
                    job.files[index].reason = "cancelado antes de empezar"
                    job.cancelled += 1
                    _publish_cancelled_skip(rt, index, "cancelado antes de empezar")
                    continue

                file_started = time.perf_counter()
                job.files[index].status = "running"
                rt.current_index = index
                rt.file_engine.pop(index, None)
                rt.file_review_counts.pop(index, None)
                file_cancel = threading.Event()
                rt.file_cancel_events[index] = file_cancel
                try:
                    result = process_batch(
                        rt.input_root,
                        Path(job.output),
                        only_files=[path],
                        skip_existing=False,
                        cancel=file_cancel,
                        file_index_offset=index,
                    )
                    seconds = time.perf_counter() - file_started
                    was_cancelled = (
                        file_cancel.is_set() or index in rt.file_cancel_requested
                    )
                    if was_cancelled:
                        # Never trust whatever the sink's `file_done` handler
                        # already stashed here: `process_batch`'s own worker
                        # catches the `ConversionError("cancelled")` (or
                        # "cancelled; partial output written to …") this
                        # file's cancel token raises internally and passes
                        # `str(exc)` straight through as `reason` (see
                        # `batch/processor.py`'s per-file worker) — a raw
                        # status token or an internal exception string, never
                        # meant to reach a user (see M4). This branch already
                        # knows definitively *why* the file stopped, so it
                        # always writes the real Spanish reason over
                        # whatever that was.
                        job.files[index].status = "cancelled"
                        job.files[index].reason = "cancelado tras la página en curso"
                        job.cancelled += 1
                    elif result.failed:
                        job.files[index].status = "failed"
                        job.files[index].reason = (
                            job.files[index].reason or "conversion failed"
                        )
                        job.failed += 1
                    elif result.warn:
                        # The sink's `file_done` handler above already filled
                        # in `.reason` with what was actually skipped.
                        job.files[index].status = "warn"
                        job.warn += 1
                    else:
                        job.files[index].status = "ok"
                        job.ok += 1
                    job.files[index].seconds = round(seconds, 2)
                except Exception as exc:  # soft-fail the job, like the CLI batch
                    was_cancelled = (
                        file_cancel.is_set() or index in rt.file_cancel_requested
                    )
                    if was_cancelled:
                        job.files[index].status = "cancelled"
                        job.files[index].reason = "cancelado tras la página en curso"
                        job.cancelled += 1
                    else:
                        logger.warning("Job %s: file %s failed: %s", job_id, path, exc)
                        job.files[index].status = "failed"
                        job.files[index].reason = str(exc)
                        job.failed += 1
                finally:
                    _fill_file_facts(rt, index)
                    file_cancel.set()  # release process_batch's cancel watcher
                    rt.current_index = None
                    rt.file_cancel_events.pop(index, None)
                    rt.file_cancel_requested.discard(index)
        finally:
            # `job.status` flips to a terminal value here, not right after the
            # loop above — a poller (`GET /api/jobs/{id}`, or a test standing
            # in for one) that treats a terminal status as "safe to assume
            # `jobs_history.json` already has this job" would otherwise win a
            # real race: this whole `finally` runs in the same thread, but a
            # concurrent reader sees `job.status` the instant it changes,
            # which used to happen several statements (and, for a large job,
            # noticeably more wall-clock time) before `_persist_history(job)`
            # at the very end of this block ever ran.
            job.status = "cancelled" if rt.cancel_event.is_set() else "done"
            job.seconds = round(time.perf_counter() - started, 2)
            # `rt.usd_so_far or None` used to stand for this, but that turns a
            # genuine zero (every cloud call priced, nothing billed — or no
            # cloud calls at all) into the same `None` a truly unpriced model
            # reports, so "no spend" and "we don't know" read identically to
            # the UI (see N16). `events.usd_unknown()` — read before
            # `set_sink(None)` below resets it for the next job — carries the
            # actual distinction.
            job.usd = None if events.usd_unknown() else round(rt.usd_so_far, 4)
            job.finished = datetime.now(UTC).isoformat()
            _publish(
                rt,
                {
                    "type": "job_done",
                    "ok": job.ok,
                    "warn": job.warn,
                    "failed": job.failed,
                    "cancelled": job.cancelled,
                    "seconds": job.seconds,
                    "usd": job.usd,
                },
            )
            events.set_sink(None)
            _ENGINE_LOGGER.removeHandler(log_handler)
            _restore_config(snapshot)
            _persist_history(job)


def create_job(
    *,
    path: str | None,
    inspect_id: str | None,
    output: str,
    pipeline: Pipeline,
    mode: str,
    preset_id: str | None = None,
) -> Job:
    output_dir = Path(output).expanduser()
    if not output_dir.is_absolute():
        raise ValueError(
            f"output must be an absolute path (or start with ~): {output!r}"
        )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_root, files = _resolve_job_input(
        path=path, inspect_id=inspect_id, output=output_dir
    )
    # Same map `process_batch` itself computes from these same inputs — kept
    # on the `_Runtime`, not written onto `JobFileState.output_path` yet (see
    # below), so anything internal that needs "where will/did this file's
    # markdown land" (`_output_path_for`: retry, page read/write, asset/
    # original resolution) never waits for that file to actually finish
    # converting.
    planned = plan_output_paths(files, input_root, output_dir)

    job_id = str(uuid.uuid4())
    job = Job(
        job_id=job_id,
        status="queued",
        output=str(output_dir),
        pipeline=pipeline,
        mode=mode,
        input=path if path is not None else str(input_root),
        preset_id=preset_id,
        files=[
            JobFileState(
                index=i,
                path=str(p),
                status="pending",
                kind=_classify_file_kind(p),
                # `None` until `_fill_file_facts` sets it from a real file on
                # disk — "once one exists" (`JobFileState.output_path`'s own
                # docstring), never wherever this file's output *would* land
                # if it ran. A file skipped by a cancel before it ever started
                # (`cancel_file()`/`_run_job`'s between-files check) never
                # reaches `_fill_file_facts`, so it stays `None` — see
                # `TestCancel.test_cancel_file_while_queued_is_skipped_without_running`.
            )
            for i, p in enumerate(files)
        ],
    )
    rt = _Runtime(job=job, input_root=input_root, files=files, planned_outputs=planned)
    with _jobs_lock:
        _jobs[job_id] = rt

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    rt.thread = thread
    thread.start()
    return job


# ---------------------------------------------------------------------------
# Job history — `jobs_history.json` in the same folder as presets/settings/
# folder_presets (see `HISTORY_FILE` above). A finished job (`_run_job`'s
# `finally`, or `retry()` once it has updated one) is persisted here so a
# Historial screen survives a server restart, which `_jobs` (in-memory,
# same as every other store this module owns) never could — see this
# module's own docstring on why that in-memory-only choice is fine for
# *running* jobs but not for ones a user wants to look back at later.
# ---------------------------------------------------------------------------


def _load_history_raw() -> list[dict[str, Any]]:
    if not HISTORY_FILE.is_file():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Bad %s, ignoring: %s", HISTORY_FILE, exc)
        return []
    return data if isinstance(data, list) else []


def _find_history_raw(job_id: str) -> dict[str, Any] | None:
    for record in _load_history_raw():
        if record.get("job_id") == job_id:
            return record
    return None


def _persist_history(job: Job) -> None:
    """Upsert `job` into `jobs_history.json` by `job_id`."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        records = [r for r in _load_history_raw() if r.get("job_id") != job.job_id]
        records.append(job.model_dump())
        HISTORY_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist job history for %s: %s", job.job_id, exc)


def list_jobs(limit: int | None = None) -> list[Job]:
    """Every job, newest first — in-memory ones (queued/running/just
    finished, not yet on disk this call) take priority over a same-id
    history record, which is only ever slightly behind (`_persist_history`
    runs at the very end of `_run_job`)."""
    combined: dict[str, Job] = {}
    for record in _load_history_raw():
        job_id = record.get("job_id")
        if not job_id:
            continue
        try:
            combined[job_id] = Job.model_validate(record)
        except Exception as exc:
            logger.debug("Skipping bad history record %s: %s", job_id, exc)
    with _jobs_lock:
        live = {job_id: rt.job for job_id, rt in _jobs.items()}
    combined.update(live)
    ordered = sorted(
        combined.values(),
        key=lambda j: j.finished or j.started or "",
        reverse=True,
    )
    return ordered[:limit] if limit is not None else ordered


def get_job(job_id: str) -> Job | None:
    rt = _jobs.get(job_id)
    if rt is not None:
        return rt.job
    record = _find_history_raw(job_id)
    if record is None:
        return None
    try:
        return Job.model_validate(record)
    except Exception:
        return None


def _rehydrate_runtime(job_id: str) -> _Runtime | None:
    """Bring a job back from `jobs_history.json` into `_jobs` so retry/
    original-preview/page-read work on it the same as a job still in memory.

    `input_root` is re-derived from `Job.input` with the exact same
    `normalize_batch_input` a live job's `_resolve_files` used — for a
    single-file job that is the file's *parent* directory, not the file
    itself (a plain `Path(job.input)` here made every relative-path
    computation in `_output_path_for`/`process_batch` collapse to "." and
    raise, since `source.relative_to(source)` is empty). Good enough for as
    long as the job's own files still live where they did — a moved/deleted
    source file simply fails that one operation, same as it would for a job
    still in memory.
    """
    record = _find_history_raw(job_id)
    if record is None:
        return None
    try:
        job = Job.model_validate(record)
    except Exception as exc:
        logger.warning("Could not rehydrate job %s from history: %s", job_id, exc)
        return None
    if job.input:
        input_root, _default_out, _only_files = normalize_batch_input(
            Path(job.input).expanduser()
        )
    else:
        input_root = Path(job.output)
    files = [Path(f.path) for f in job.files]
    planned = {Path(f.path): Path(f.output_path) for f in job.files if f.output_path}
    rt = _Runtime(job=job, input_root=input_root, files=files, planned_outputs=planned)
    with _jobs_lock:
        rt = _jobs.setdefault(job_id, rt)
    return rt


def _get_runtime(job_id: str) -> _Runtime:
    rt = _jobs.get(job_id)
    if rt is None:
        rt = _rehydrate_runtime(job_id)
    if rt is None:
        raise KeyError(job_id)
    return rt


def pause_job(job_id: str) -> Job | None:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    rt.pause_event.set()
    rt.job.status = "paused"
    return rt.job


def resume_job(job_id: str) -> Job | None:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    rt.pause_event.clear()
    if rt.job.status == "paused":
        rt.job.status = "running"
    return rt.job


class FileNotCancellableError(Exception):
    """Raised by `cancel_file()` when the file is already finished (ok/warn/
    failed/cancelled) — `server/app.py` turns this into a 409, since there is
    nothing left to stop."""


def cancel_job(job_id: str) -> Job | None:
    """Stop the job after the *current page*, not the current file.

    Sets the job-wide `cancel_event` (checked between files, same as
    before) **and**, if a file is running right now, that one file's own
    cancel token — `process_batch(cancel=...)` forwards it straight into
    `converter/pdf_ocr.py`'s per-page OCR loop, so the in-flight file stops
    after its current page and keeps whatever it already transcribed
    (`_convert_one`'s `interrupted` branch: the same "conversión incompleta"
    banner a file-timeout produces), rather than finishing the whole file
    first.
    """
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    rt.cancel_event.set()
    rt.pause_event.clear()  # a paused job must still see the cancel
    if rt.current_index is not None:
        current = rt.file_cancel_events.get(rt.current_index)
        if current is not None:
            current.set()
    return rt.job


def cancel_file(job_id: str, file_index: int) -> Job:
    """Stop one file without touching the rest of the job.

    * queued (`"pending"`): skipped outright — marked `"cancelled"`, no
      output, `reason="cancelado antes de empezar"`. `_run_job`'s loop checks
      for this status and skips the file when it gets there.
    * running: its own cancel token is set, so it stops after the current
      page (same mechanism as `cancel_job`, just scoped to this one file) —
      `_run_job` labels it `"cancelled"` once `process_batch` returns/raises
      for it.
    * already finished (ok/warn/failed/cancelled): `FileNotCancellableError`
      (409 — nothing left to stop).

    The job itself is untouched either way: `cancel_event` is never set, so
    the loop moves on to the next file exactly as if this one had converted
    normally.
    """
    rt = _get_runtime(job_id)
    if file_index < 0 or file_index >= len(rt.job.files):
        raise IndexError(file_index)
    file_state = rt.job.files[file_index]
    if file_state.status == "pending":
        file_state.status = "cancelled"
        file_state.reason = "cancelado antes de empezar"
        rt.job.cancelled += 1
        _publish_cancelled_skip(rt, file_index, "cancelado antes de empezar")
        return rt.job
    if file_state.status == "running":
        rt.file_cancel_requested.add(file_index)
        token = rt.file_cancel_events.get(file_index)
        if token is not None:
            token.set()
        return rt.job
    raise FileNotCancellableError(file_index)


def subscribe(job_id: str) -> tuple[list[dict[str, Any]], queue.Queue] | None:
    """Replay buffer + a live queue for `WS /api/jobs/{id}/events`."""
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    q: queue.Queue = queue.Queue()
    with rt.lock:
        buffered = list(rt.event_log)
        rt.subscribers.append(q)
    return buffered, q


def unsubscribe(job_id: str, q: queue.Queue) -> None:
    rt = _jobs.get(job_id)
    if rt is None:
        return
    with rt.lock:
        if q in rt.subscribers:
            rt.subscribers.remove(q)


def shutdown(timeout: float = 10.0) -> None:
    """Cancel every job and join its background thread.

    Called from the FastAPI lifespan's shutdown phase (`server/app.py`) so
    `make app` exits cleanly, and from the test suite's per-test teardown so
    a `_run_job` thread a test started never outlives that test — see
    `_Runtime.thread`'s docstring for why an unjoined one is dangerous at
    interpreter teardown, not just untidy.
    """
    with _jobs_lock:
        runtimes = list(_jobs.values())
    for rt in runtimes:
        rt.cancel_event.set()
        rt.pause_event.clear()  # a paused job must still see the cancel
    for rt in runtimes:
        if rt.thread is not None:
            rt.thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# Page detail (Revisar screen) and single-page retry
# ---------------------------------------------------------------------------

_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\s*$", re.MULTILINE)
# A page's figure blocks (see converter.figures.figure_block/insert_figure_blocks)
# are appended after the OCR text within the same "## Page N" section. A
# single-page retry only re-runs OCR/review, never the figure pass, so its
# replacement text must splice back in whatever figure blocks the page already
# had — otherwise it silently deletes them from the file (see B5).
_FIGURE_HEADING_RE = re.compile(r"^### Figura \d+\.\d+\s*$", re.MULTILINE)
# The heading `converter.pdf_ocr._format_ocr_prose`/`compose_pdf_markdown` write
# right before a page's OCR text. A single-page retry only regenerates *this*
# heading's body — everything before it (the page heading, any native-text
# chunk `compose_pdf_markdown` put ahead of the OCR heading, or a line a user
# typed there via `PUT .../pages/{n}`) and everything after it (table blocks,
# figure blocks, or anything else a user's saved edit added there) is not
# OCR output and must survive the retry untouched (see N10: before this, only
# figure blocks were spliced back, so a saved edit anywhere else in the page —
# before the OCR heading, or after the figures — was silently dropped).
_OCR_HEADING_RE = re.compile(r"^### OCR\s*$", re.MULTILINE)
# Where the old OCR body ends and the preserved tail begins: the next
# structural heading a later pipeline stage (or a saved edit sitting after
# one) puts after the OCR heading — a figure block, a table block, or
# another `### `/`## ` section. Deliberately not "any '#'" — OCR/review text
# is prose the model can render with a literal leading '#' too (a heading it
# saw on the scanned page), and that must stay part of the regenerated OCR
# body, not get reclassified as the preserved tail.
_NEXT_HEADING_RE = re.compile(
    r"^(?:### Figura \d+\.\d+|### Table\b|#{2,3} )\s*$", re.MULTILINE
)


def _output_path_for(rt: _Runtime, file_index: int) -> Path | None:
    if file_index < 0 or file_index >= len(rt.files):
        return None
    source = rt.files[file_index]
    planned = rt.planned_outputs.get(source)
    if planned is not None:
        return planned
    from twomarkdown.batch.processor import _mirror_output_path

    return _mirror_output_path(source, rt.input_root, Path(rt.job.output))


def _slice_page(markdown: str, page: int) -> tuple[str, int | None]:
    """`(this page's slice, total pages found)`. No markers -> the whole file,
    total `None` — the caller (server/app.py) uses that to tell the UI this
    file has no pages to navigate (not "1 page" — that would say there is
    exactly one, when there is no pagination at all), and returns the whole
    file's markdown either way rather than 404ing (see the review-scope
    note in `docs/desktop-app.md`: Revisar used to only ever show scanned
    PDFs, since a page count that could not tell "0/1 total" apart from "no
    pagination" read as "nothing to show" to the frontend)."""
    matches = list(_PAGE_HEADING_RE.finditer(markdown))
    if not matches:
        return markdown, None
    for i, match in enumerate(matches):
        if int(match.group(1)) != page:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        return markdown[start:end].strip() + "\n", len(matches)
    return "", len(matches)


def read_page(
    job_id: str, file_index: int, page: int
) -> tuple[bytes | None, str, int | None]:
    """`(page PNG bytes at 120 dpi or None, markdown slice, page_count)`.
    `page_count` is `None` for a file with no `## Page N` markers — the whole
    file's markdown is still returned (see `_slice_page`'s own docstring)."""
    rt = _get_runtime(job_id)
    output_path = _output_path_for(rt, file_index)
    markdown = (
        output_path.read_text(encoding="utf-8")
        if output_path and output_path.exists()
        else ""
    )
    slice_text, page_count = _slice_page(markdown, page)

    png_bytes: bytes | None = None
    source = rt.files[file_index] if 0 <= file_index < len(rt.files) else None
    if source is not None and source.suffix.lower() == ".pdf":
        try:
            with fitz.open(source) as doc:
                if 1 <= page <= doc.page_count:
                    zoom = 120 / 72.0
                    pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                    png_bytes = pix.tobytes("png")
        except Exception as exc:
            logger.debug("Could not render page %s of %s: %s", page, source, exc)
    return png_bytes, slice_text, page_count


def resolve_asset_path(job_id: str, file_index: int, name: str) -> Path:
    """Resolve a figure asset's on-disk path for `GET .../assets/{name}`.

    `name` is the asset path relative to the *file's own output folder*
    (`output_md.parent` — see `batch/processor.py`'s `assets_dir =
    output_md.parent / f"{output_md.stem}_assets"`), matching what the
    Markdown itself references (`converter/figures.py`'s `figure_block`
    writes that same relative path) — so it always starts with the
    `<stem>_assets/` segment, e.g. `"Tema 1_assets/Tema 1-fig-p1-1.png"`.

    Resolved against `output_md.parent` rather than `assets_dir` directly
    for that reason, then checked against `assets_dir` so a `name` crafted
    with `../` segments cannot escape it to read arbitrary files elsewhere
    on disk (job output, or anything else the server process can read).
    """
    rt = _get_runtime(job_id)
    output_path = _output_path_for(rt, file_index)
    if output_path is None:
        raise KeyError(file_index)
    assets_dir = (output_path.parent / f"{output_path.stem}_assets").resolve()
    candidate = (output_path.parent / name).resolve()
    try:
        candidate.relative_to(assets_dir)
    except ValueError:
        raise PermissionError(name) from None
    if not candidate.is_file():
        raise FileNotFoundError(name)
    return candidate


def write_page(job_id: str, file_index: int, page: int, markdown: str) -> str:
    """Splice an edited page slice back into its file; return the new slice.

    The frontmatter's `converted_at`/`char_count` are refreshed to match the
    new body (see `frontmatter.refresh_frontmatter_after_edit`) — otherwise a
    page-level write leaves the file lying about its own content (B5).

    Marks `page` as edited on `rt.edited_pages` — a subsequent `retry()` for
    this same page checks that before regenerating it from a fresh OCR pass,
    so a saved edit is never silently overwritten (see N10).
    """
    from twomarkdown.frontmatter import refresh_frontmatter_after_edit

    rt = _get_runtime(job_id)
    output_path = _output_path_for(rt, file_index)
    if output_path is None or not output_path.exists():
        raise FileNotFoundError(f"no output for file {file_index}")
    current = output_path.read_text(encoding="utf-8")
    matches = list(_PAGE_HEADING_RE.finditer(current))
    if not matches:
        output_path.write_text(
            refresh_frontmatter_after_edit(markdown), encoding="utf-8"
        )
        rt.edited_pages.setdefault(file_index, set()).add(page)
        return markdown
    for i, match in enumerate(matches):
        if int(match.group(1)) != page:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(current)
        new_content = current[:start] + markdown.rstrip() + "\n\n" + current[end:]
        new_content = refresh_frontmatter_after_edit(new_content)
        output_path.write_text(new_content, encoding="utf-8")
        rt.edited_pages.setdefault(file_index, set()).add(page)
        return markdown
    raise KeyError(f"page {page} not found in {output_path}")


def _recompute_job_totals(job: Job) -> None:
    """Recompute `Job.ok/warn/failed` from `Job.files[]`.

    `_run_job` increments these counters itself as each file finishes, but a
    retry (both branches below) only ever updates the one retried file's
    `JobFileState.status` — nothing recomputed the job-level aggregate to
    match, so the summary line kept describing the *original* run even after
    a retry changed a file's outcome (see N14: a page retried into "warn"
    left the row showing "!" while the summary underneath still read
    "1 ✓ · 0 ! · 0 ✕"). Called at the end of `retry()` so the two always
    agree with whatever `Job.files[]` currently says, regardless of how many
    retries have happened.
    """
    counts = {"ok": 0, "warn": 0, "failed": 0, "cancelled": 0}
    for file_state in job.files:
        if file_state.status in counts:
            counts[file_state.status] += 1
    job.ok = counts["ok"]
    job.warn = counts["warn"]
    job.failed = counts["failed"]
    job.cancelled = counts["cancelled"]


class UnsavedPageEditError(Exception):
    """Raised by `retry()` when it would silently overwrite a page-level edit
    saved (via `PUT .../pages/{n}`) since that page was last converted (see
    N10). `server/app.py` turns this into a 409 naming the affected pages;
    the caller re-sends the same `JobRetryRequest` with `confirm=True` to
    proceed anyway (accepting the loss) or backs off (keeping the edit)."""

    def __init__(self, file_index: int, pages: set[int]) -> None:
        self.file_index = file_index
        self.pages = pages
        super().__init__(
            f"file {file_index} has unsaved edits on page(s) "
            f"{sorted(pages)} that would be overwritten by this retry"
        )


def retry(job_id: str, req: JobRetryRequest) -> Job:
    """Re-run one file, or one page of it, with `pipeline_patch` merged in.

    Refuses (`UnsavedPageEditError`) rather than silently discarding a saved
    page edit — see that class's docstring and N10 — unless `req.confirm` is
    set. A `req.page is None` (whole-file) retry checks every page of the
    file, since it regenerates all of them.

    Two things a naive read of the old code missed (see M6):

    * No sink was ever attached here, so a retry ran with `events.has_sink()`
      false the whole time — every `file_started`/`file_done`/`page_done`/
      `log` event the engine emits during it (including the one that would
      have told the caller "no credit left") silently went nowhere. Attached
      for the same span `_run_job` attaches it for, below.
    * `pipeline` (the patch merged onto the job's saved one) was applied to
      the engine's global config for the retry, then thrown away — so on
      success the job kept reporting its *original* pipeline, and a second
      retry started from that original again, not from what the first retry
      actually changed. Persisted back onto `rt.job.pipeline` now, but only
      once the retry has actually finished without raising.

    A third, page-level-only: `_retry_single_page` had nothing that wrote
    this attempt's own status/reason/seconds onto `Job.files[req.file_index]`
    — `_make_sink`'s `file_done` handling is what does that now, fed by the
    `events.end_file()` call `_retry_single_page` makes at the end of its own
    run (see M6). Without it the field kept reporting whatever an earlier
    attempt (the very one the user asked to retry away from) had left there.

    Works for a historical job too (one no longer in `_jobs`, loaded back
    from `jobs_history.json`) — `_get_runtime` rehydrates it into `_jobs`
    first, same as `read_page`/`get_original_info`. `req.preset_id`, when
    given, replaces `req.pipeline_patch` outright: the "re-convert this file
    with another model" flow picks a saved preset/model, not a hand-built
    patch dict. Refuses (`ValueError`, same as `server/app.py`'s own
    `_reject_if_blocked` for `POST /api/jobs`) a pipeline this machine's GPU
    could not actually hold resident, so a retry can never end up in the
    OOM/malformed-completion failure `POST /api/jobs` already refuses up
    front — nothing app.py's `post_jobs` handler could have caught for a
    retry, since it never calls the same guard for `/retry`.
    """
    rt = _get_runtime(job_id)
    if req.file_index < 0 or req.file_index >= len(rt.files):
        raise IndexError(req.file_index)

    edited = rt.edited_pages.get(req.file_index) or set()
    if not req.confirm:
        at_risk = edited if req.page is None else (edited & {req.page})
        if at_risk:
            raise UnsavedPageEditError(req.file_index, at_risk)

    patch = req.pipeline_patch
    if req.preset_id is not None:
        from twomarkdown.server import presets

        preset = presets.get_preset(req.preset_id)
        if preset is None:
            raise ValueError(f"preset_id {req.preset_id!r} not found")
        patch = preset.pipeline.model_dump()

    pipeline = rt.job.pipeline.model_copy(update=patch or {})
    _reject_if_blocked_pipeline(pipeline)
    source = rt.files[req.file_index]

    log_handler = _EngineLogHandler(level=logging.INFO)
    with _ENGINE_LOCK:
        events.set_sink(_make_sink(rt))
        _ENGINE_LOGGER.addHandler(log_handler)
        snapshot = _apply_pipeline(pipeline)
        rt.file_engine.pop(req.file_index, None)
        rt.file_review_counts.pop(req.file_index, None)
        try:
            if req.page is None:
                result = process_batch(
                    rt.input_root,
                    Path(rt.job.output),
                    only_files=[source],
                    skip_existing=False,
                    file_index_offset=req.file_index,
                )
                if result.failed:
                    rt.job.files[req.file_index].status = "failed"
                elif result.warn:
                    rt.job.files[req.file_index].status = "warn"
                else:
                    rt.job.files[req.file_index].status = "ok"
                    rt.job.files[req.file_index].reason = None
            else:
                _retry_single_page(rt, req.file_index, req.page)
            rt.job.pipeline = pipeline
            _fill_file_facts(rt, req.file_index)
            _recompute_job_totals(rt.job)
            # This retry just regenerated the page(s) above — any edit
            # tracked for them is gone now regardless of outcome, so stop
            # guarding it (see N10). A whole-file retry clears every page.
            if req.page is None:
                rt.edited_pages.pop(req.file_index, None)
            else:
                edited_now = rt.edited_pages.get(req.file_index)
                if edited_now is not None:
                    edited_now.discard(req.page)
                    if not edited_now:
                        rt.edited_pages.pop(req.file_index, None)
        finally:
            events.set_sink(None)
            _ENGINE_LOGGER.removeHandler(log_handler)
            _restore_config(snapshot)
    _persist_history(rt.job)
    return rt.job


def _reject_if_blocked_pipeline(pipeline: Pipeline) -> None:
    """Same resident-memory refusal `server/app.py`'s `_reject_if_blocked`
    gives `POST /api/jobs` — mirrored here (not imported) so `jobs.py` never
    depends on `server/app.py`. Raises `ValueError`, which `post_job_retry`
    already turns into a 422."""
    from twomarkdown.batch.estimate import _blocked_info, _effective_figure_model

    review_model = pipeline.review_model or None
    figure_model = (
        _effective_figure_model(pipeline.ocr_model, pipeline.figure_model, review_model)
        if pipeline.describe_figures
        else None
    )
    blocked = _blocked_info(pipeline.ocr_model, figure_model, review_model)
    if blocked is not None:
        raise ValueError(f"{blocked.title}: {blocked.body}")


def _existing_page_context(rt: _Runtime, file_index: int, page: int) -> tuple[str, str]:
    """`(prefix, tail)` around the page's current `### OCR` section.

    `prefix` is everything in the page's current slice before the `### OCR`
    heading (the `## Page N` heading itself, any native-text chunk
    `compose_pdf_markdown` placed ahead of the OCR heading, or a line a user
    added there via `PUT .../pages/{n}`). `tail` is everything from the next
    heading after the OCR body onward — figure blocks (see
    `converter.figures`), table blocks, or any other section a saved edit
    added after the OCR text. Both are handed back to `_retry_single_page`
    unchanged so a page-level retry — which only re-runs OCR/review, never
    the rest of the pipeline — regenerates *only* the OCR body and never
    silently drops what a user saved around it (see N10; this used to only
    preserve `### Figura …` blocks specifically, so a saved edit anywhere
    else in the page was lost).

    Both empty when the page has no `### OCR` heading yet (e.g. this is the
    first time this page is ever processed, or the file/page cannot be read
    yet) — the caller falls back to its own default page heading in that
    case.
    """
    output_path = _output_path_for(rt, file_index)
    if output_path is None or not output_path.exists():
        return "", ""
    current_slice, _ = _slice_page(output_path.read_text(encoding="utf-8"), page)
    ocr_match = _OCR_HEADING_RE.search(current_slice)
    if not ocr_match:
        return "", ""
    prefix = current_slice[: ocr_match.start()]
    next_heading = _NEXT_HEADING_RE.search(current_slice, ocr_match.end())
    tail = current_slice[next_heading.start() :].rstrip() if next_heading else ""
    return prefix, tail


def _retry_single_page(rt: _Runtime, file_index: int, page: int) -> None:
    from twomarkdown.agents.page_review import (
        describe_changes_structured,
        review_enabled,
        review_page,
    )
    from twomarkdown.converter import ocr as ocr_mod
    from twomarkdown.converter.clean import clean_markdown
    from twomarkdown.converter.pdf_ocr import _render_page_pixmap

    source = rt.files[file_index]
    if source.suffix.lower() != ".pdf":
        raise ValueError("page-level retry only applies to PDF sources")

    # Captured before OCR/review run so it reflects what is on disk right now,
    # not anything this retry is about to write.
    prefix, tail = _existing_page_context(rt, file_index, page)

    # This retry's own outcome — a fresh soft-failure reason (or none) and its
    # own timing — must replace whatever a previous attempt left on
    # `Job.files[file_index]`, not sit alongside it (see M6). `begin_engine_record`
    # resets the thread-local soft-failure slot `review_page` below writes to via
    # `ocr_mod.record_soft_failure`; without it a stale reason from an earlier
    # file processed on this same worker thread could leak in here.
    ocr_mod.begin_engine_record()
    started = time.perf_counter()

    ocr_fn = None
    if conversion_config.ocr_backend == "ollama":
        from twomarkdown.agents.image_ocr import ocr_image_bytes_llm

        ocr_fn = ocr_image_bytes_llm
    else:
        ocr_fn = ocr_mod.extract_text_with_tesseract

    with fitz.open(source) as doc:
        if not (1 <= page <= doc.page_count):
            raise IndexError(page)
        png_bytes = _render_page_pixmap(doc, page - 1)

    with events.stage("ocr", page=page):
        text = ocr_mod.ocr_image_bytes(
            png_bytes,
            ocr_fn=ocr_fn,
            force_llm=conversion_config.ocr_backend == "ollama",
        ).strip()

    changes: list[dict[str, Any]] = []
    if text and review_enabled():
        reviewed, change_lines = review_page(text)
        if change_lines:
            changes = describe_changes_structured(text, reviewed)
        text = reviewed

    # Same deterministic pass the first conversion runs on every page (see
    # `_convert_one`'s `_clean(markdown)`) — without it a re-read writes
    # whatever raw delimiter form the model happened to use this time
    # (e.g. `\( … \)`), diverging from the rest of the document (see N1).
    text = clean_markdown(text).strip()

    new_page = (
        f"{prefix}### OCR\n\n{text}"
        if prefix
        else f"## Page {page}\n\n### OCR\n\n{text}"
    )
    if tail:
        new_page = f"{new_page}\n\n{tail}"

    write_page(rt.job.job_id, file_index, page, new_page)
    events.page_done(
        file_index,
        page,
        engine=conversion_config.ocr_backend,
        seconds=0.0,
        fallback=False,
        review_changes=changes,
    )

    # Reflect *this* attempt's own outcome onto the job's file record — see
    # `_make_sink`'s `file_done` handling above, the only place this reaches
    # `Job.files[]` for a page-level retry (see M6).
    soft_reason = ocr_mod.soft_failure_reason()
    events.end_file(
        file_index,
        "warn" if soft_reason else "ok",
        time.perf_counter() - started,
        reason=soft_reason,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/files/{i}/original — the "ver original" panel
# ---------------------------------------------------------------------------


def get_original_info(
    job_id: str, file_index: int
) -> tuple[JobFileKind, Path, int | None]:
    """`(kind, source path, pages)` for the original-preview endpoint.

    `pages` is only ever set for `kind == "pdf"` (a fresh `fitz.open` count,
    not whatever `JobFileState.pages` currently holds — this must work even
    before the job has converted the file at all). `server/app.py` decides
    what to send back from `kind`: the raw bytes for an image, `{kind,
    pages}` for a PDF (it pages through the existing page-image endpoint
    itself from there), or `{kind, previewable: false, path}` for anything
    else, so the app can offer "abrir con la app del sistema" instead.
    """
    rt = _get_runtime(job_id)
    if file_index < 0 or file_index >= len(rt.files):
        raise IndexError(file_index)
    source = rt.files[file_index]
    kind = rt.job.files[file_index].kind or _classify_file_kind(source)
    pages: int | None = None
    if kind == "pdf":
        try:
            with fitz.open(source) as doc:
                pages = doc.page_count
        except Exception as exc:
            logger.debug("Could not open %s for original preview: %s", source, exc)
    return kind, source, pages


# ---------------------------------------------------------------------------
# POST /api/open, POST /api/reveal — open/reveal a path with the OS default
# app, guarded to a job's own input/output roots or the user's home.
# ---------------------------------------------------------------------------


def _allowed_open_roots(job_id: str | None) -> list[Path]:
    roots = [Path.home().resolve()]
    if job_id is not None:
        candidates = [_jobs.get(job_id) or _rehydrate_runtime(job_id)]
    else:
        # No job named — the input/output roots of every job this process
        # currently knows about (in-memory only; not worth loading the whole
        # history file just to widen this check) are still fair game, on top
        # of the user's home directory.
        with _jobs_lock:
            candidates = list(_jobs.values())
    for rt in candidates:
        if rt is not None:
            roots.append(rt.input_root.resolve())
            roots.append(Path(rt.job.output).resolve())
    return roots


def is_path_allowed(path: Path, *, job_id: str | None = None) -> bool:
    """Whether `path` sits under one of the roots `POST /api/open`/`/reveal`
    may touch — a job's own input root, its output folder, or the user's
    home directory. Never arbitrary: these two endpoints shell out to the OS
    (`open`/`open -R`), so an unguarded path would let any caller on
    localhost open anything the server process can read."""
    resolved = path.resolve()
    for root in _allowed_open_roots(job_id):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def open_path(
    path: str, *, job_id: str | None = None, reveal: bool = False
) -> str | None:
    """`open <path>` (or `open -R <path>` to reveal it in Finder). Returns an
    error string, or `None` on success. macOS-only (`open` is a macOS binary);
    every other platform reports that plainly rather than trying `xdg-open`/
    `explorer` and guessing whether it worked."""
    import platform
    import subprocess

    candidate = Path(path).expanduser()
    if not is_path_allowed(candidate, job_id=job_id):
        return "path is outside the allowed job/output/home roots"
    if not candidate.exists():
        return "path does not exist"
    if platform.system() != "Darwin":
        return "opening files is only supported on macOS"
    args = ["open", "-R", str(candidate)] if reveal else ["open", str(candidate)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10.0)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "open failed").strip()
    return None
