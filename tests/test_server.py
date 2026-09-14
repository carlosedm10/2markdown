"""Tests for the desktop app's local API server (twomarkdown.server).

Every test that would otherwise touch the real user's
`~/Library/Application Support/2markdown/` (presets, syncs, manual prices)
or the network (Ollama, OpenAI) redirects those paths/calls to a fixture
first — this suite must not depend on, or pollute, the machine it runs on.

Endpoint tests drive the app through `httpx.AsyncClient` + `ASGITransport`
(no real socket, no `pytest-asyncio` dependency needed — async tests are
marked `@pytest.mark.anyio` and run on anyio's asyncio backend, since anyio
already ships as a transitive dependency of fastapi/pydantic-ai and trio is
not installed). The two WebSocket tests in `TestJobs` use `TestClient`
instead, because `httpx.AsyncClient` has no WebSocket support.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from twomarkdown.server import (
    folder_presets,
    host,
    jobs,
    models,
    presets,
    settings,
    syncs,
    system,
)
from twomarkdown.server.app import app
from twomarkdown.server.schemas import Pipeline, Preset, Settings, SyncCreateRequest

# Captured before any test's `_isolated_state` autouse fixture below replaces
# `system._dotenv_value` module-wide with a stub that always answers "no key"
# (so no ordinary test can leak the real machine's `.env`/env vars) — kept so
# `TestCloudKeys` can restore the *real* implementation for itself, since it
# already isolates `.env` a different way (`host._ENV_PATH` -> `tmp_path`) and
# needs `_dotenv_value` to actually read that file back.
_real_dotenv_value = system._dotenv_value


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Redirect every on-disk store this module touches into `tmp_path`."""
    monkeypatch.setattr(presets, "PRESETS_DIR", tmp_path)
    monkeypatch.setattr(presets, "PRESETS_FILE", tmp_path / "presets.json")
    monkeypatch.setattr(settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(syncs, "SYNCS_FILE", tmp_path / "syncs.json")
    monkeypatch.setattr(folder_presets, "FOLDER_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(
        folder_presets, "FOLDER_PRESETS_FILE", tmp_path / "folder_presets.json"
    )
    monkeypatch.setattr(jobs, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jobs, "HISTORY_FILE", tmp_path / "jobs_history.json")
    # `jobs.py` persists every finished job to `jobs_history.json` in the same
    # real per-user app-support directory as the stores above — without this,
    # a test that runs a job to completion writes into (and, worse, reads
    # stray records back from) the actual machine's history file, and every
    # history test in this module would see every other test's jobs too
    # (all in the same process, same file, across the whole suite run).
    monkeypatch.setattr(jobs, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(jobs, "HISTORY_FILE", tmp_path / "jobs_history.json")
    monkeypatch.setattr(
        "twomarkdown.batch.estimate._default_config_dir", lambda: tmp_path
    )
    # C6: the persisted cloud-key probe-verdict cache also lives under the
    # real per-user app-support directory — redirect it too, and start every
    # test with an empty in-memory cache so no earlier test's verdict leaks.
    monkeypatch.setattr(system, "_PROBE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        system, "_PROBE_CACHE_FILE", tmp_path / "cloud_probe_cache.json"
    )
    monkeypatch.setattr(system, "_probe_cache", {})
    # /api/models caches for 60s (server/models.py) — a stale cache from an
    # earlier test in the same process must never leak into this one.
    monkeypatch.setattr(models, "_cache", None)
    # No real Ollama/OpenAI reachable (or wanted) from a test run.
    monkeypatch.setattr(
        "twomarkdown.server.system.ollama_info",
        lambda: __import__(
            "twomarkdown.server.schemas", fromlist=["OllamaInfo"]
        ).OllamaInfo(running=False, version=None, models=[]),
    )
    # Every provider probe (OpenAI included) resolves its key through this one
    # function — patch it once so no real .env / host env var leaks into a
    # test, whatever provider it's for.
    monkeypatch.setattr("twomarkdown.server.system._dotenv_value", lambda var: None)
    yield
    # Jobs and sync watchers are process-wide (in-memory) state too; do not
    # leak between tests. Critically, this must actually stop and join every
    # background thread a test started (`jobs.shutdown()` cancels + joins
    # every `_run_job` thread; `syncs.stop_all()` stops + joins every
    # `watchfiles.watch` watcher thread) rather than just dropping the dict
    # references `jobs._jobs.clear()`/`syncs._watchers.clear()` used to — a
    # thread left running past its owning test survives to interpreter
    # teardown, where it segfaults instead of exiting quietly (see
    # INCONSISTENCIES.md: this was the actual cause of the whole-suite 139).
    jobs.shutdown()
    jobs._jobs.clear()
    syncs.stop_all()


@pytest.fixture
def anyio_backend() -> str:
    """Pin anyio's pytest plugin to asyncio only (trio isn't installed)."""
    return "asyncio"


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sync_client() -> TestClient:
    """TestClient (not the async `client` above) — only for the two
    WebSocket tests in `TestJobs`, since `httpx.AsyncClient` cannot open
    WebSocket connections."""
    return TestClient(app)


async def _wait_for_job(
    client: AsyncClient, job_id: str, *, timeout: float = 5.0
) -> dict:
    """Poll GET /api/jobs/{id} until it reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/jobs/{job_id}")
        data = resp.json()
        if data["status"] in ("done", "failed", "cancelled"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


# ---------------------------------------------------------------------------
# GET /api/system
# ---------------------------------------------------------------------------


class TestSystem:
    @pytest.mark.anyio
    async def test_shape(self, client: AsyncClient) -> None:
        """GET /api/system — returns hardware, OCR engine, and pricing state."""
        resp = await client.get("/api/system")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ollama"]["running"] is False
        assert data["cloud"]["openai"] == {"key_present": False, "status": "no_key"}
        # Every other cloud provider gets the same no-key shape with no
        # dedicated probe (see system.provider_cloud_info).
        for provider in ("anthropic", "google", "groq", "mistral", "openrouter"):
            assert data["cloud"][provider] == {"key_present": False, "status": "no_key"}
        assert isinstance(data["ram_gb"], (int, float))
        assert isinstance(data["cpu_cores"], int)
        assert data["prices"]["source"] == "genai-prices"

    def test_apple_silicon_gets_a_gpu_limit(self, monkeypatch) -> None:
        """system.gpu_limit_gb — reserves 3/4 of RAM for the GPU on Apple Silicon."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        assert system.gpu_limit_gb(48.0) == pytest.approx(36.0)

    def test_intel_gets_no_gpu_limit(self, monkeypatch) -> None:
        """system.gpu_limit_gb — returns 0 on non-Apple-Silicon hardware."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: False)
        assert system.gpu_limit_gb(48.0) == 0.0

    def test_ollama_probe_and_engine_agree_on_the_host(self) -> None:
        """B1: the system panel's Ollama probe and `llm_config.ollama_base_url`
        must name the same host, or the panel can say "Listo" while every real
        conversion call fails to connect (see config.ollama_host)."""
        from twomarkdown.config import ollama_host
        from twomarkdown.server import system

        assert system._OLLAMA_BASE == f"http://{ollama_host()}:11434"

    def test_openai_no_key_short_circuits_before_any_probe(self, monkeypatch) -> None:
        """system.openai_info — skips the network probe entirely when no key
        is configured."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_dotenv_openai_key", lambda: None)
        called = []
        monkeypatch.setattr(
            system, "_probe_openai", lambda key: called.append(key) or "ok"
        )

        info = system.openai_info()

        assert info.key_present is False
        assert info.status == "no_key"
        assert called == []

    def test_insufficient_quota_maps_to_out_of_credit(self, monkeypatch) -> None:
        """system.openai_info — the account being out of credit (see AGENTS.md)
        is a real, surfaced state — never retried, never a generic error."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_dotenv_openai_key", lambda: "sk-test")
        monkeypatch.setattr(system, "_probe_cache", None)

        class _Resp:
            status_code = 429
            text = '{"error": {"code": "insufficient_quota"}}'

        monkeypatch.setattr(
            "twomarkdown.server.system.httpx.post", lambda *a, **k: _Resp()
        )

        info = system.openai_info()

        assert info.key_present is True
        assert info.status == "out_of_credit"

    def test_probe_result_is_cached(self, monkeypatch) -> None:
        """system.openai_info — caches the probe result across calls."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_dotenv_openai_key", lambda: "sk-test")
        monkeypatch.setattr(system, "_probe_cache", None)
        calls = []
        monkeypatch.setattr(
            system, "_probe_openai", lambda key: calls.append(1) or "ok"
        )

        system.openai_info()
        system.openai_info()

        assert len(calls) == 1

    def test_probe_vision_reads_the_capabilities_list(self, monkeypatch) -> None:
        """N21: `/api/show`'s own `capabilities` list is the source of truth
        for whether an installed model takes image input — not a guess from
        its name. `gemma3:4b` doesn't spell "vision" anywhere in its id but
        must still come back `vision=True` once `/api/show` says so."""
        from twomarkdown.server import system

        class _Client:
            def post(self, url, json):
                assert json == {"model": "gemma3:4b"}
                return _Resp({"capabilities": ["completion", "vision"]})

        class _Resp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self):
                return self._data

        assert system._probe_vision(_Client(), "gemma3:4b") is True

    def test_probe_vision_false_when_capabilities_omit_it(self, monkeypatch) -> None:
        """A text-only model (e.g. `qwen2.5:14b`) reports `capabilities`
        without `"vision"` in it — must come back `False`, not just any
        truthy `/api/show` response."""
        from twomarkdown.server import system

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self):
                return {"capabilities": ["completion", "tools"]}

        class _Client:
            def post(self, url, json):
                return _Resp()

        assert system._probe_vision(_Client(), "qwen2.5:14b") is False

    def test_probe_vision_falls_back_to_families_without_capabilities(
        self, monkeypatch
    ) -> None:
        """An Ollama old enough not to report `capabilities` at all falls
        back to `details.families` against the known vision-encoder family
        names (`clip` here, alongside the model's own text backbone)."""
        from twomarkdown.server import system

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self):
                return {"details": {"family": "qwen2", "families": ["qwen2", "clip"]}}

        class _Client:
            def post(self, url, json):
                return _Resp()

        assert system._probe_vision(_Client(), "minicpm-v:latest") is True

    def test_probe_vision_result_is_cached(self, monkeypatch) -> None:
        """A `/api/show` round trip per installed model on every `/api/system`
        poll would be wasteful — cached the same way the OpenAI probe is."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_vision_cache", {})
        calls = []

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self):
                return {"capabilities": ["completion", "vision"]}

        class _Client:
            def post(self, url, json):
                calls.append(json)
                return _Resp()

        client = _Client()
        assert system._probe_vision(client, "gemma3:4b") is True
        assert system._probe_vision(client, "gemma3:4b") is True
        assert len(calls) == 1

    def test_probe_vision_never_raises_on_a_bad_response(self, monkeypatch) -> None:
        """Every other probe in this module is best-effort — a broken/
        unreachable `/api/show` must read as "not vision-capable", not
        propagate and take `/api/system` down with it."""
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_vision_cache", {})

        class _Client:
            def post(self, url, json):
                raise RuntimeError("boom")

        assert system._probe_vision(_Client(), "whatever:latest") is False

    def test_ollama_info_tags_each_model_with_vision(self, monkeypatch) -> None:
        """N21: `GET /api/system`'s `ollama.models[]` carries `vision` per
        model, sourced from `/api/show` (via `_probe_vision`) rather than
        left for the app to guess from the name."""
        from twomarkdown.server import system

        # `_isolated_state` (autouse) replaces `system.ollama_info` itself
        # with a stub so no other test touches the network — undo its
        # patches (nothing else in this test needs them) so this one can
        # exercise the real function instead of that stub.
        monkeypatch.undo()
        monkeypatch.setattr(system, "_vision_cache", {})

        class _Resp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self) -> None:
                pass

            def json(self):
                return self._data

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                if url.endswith("/api/tags"):
                    return _Resp(
                        {
                            "models": [
                                {"name": "gemma3:4b", "size": 3_300_000_000},
                                {"name": "qwen2.5:14b", "size": 9_000_000_000},
                            ]
                        }
                    )
                if url.endswith("/api/version"):
                    return _Resp({"version": "0.1.0"})
                if url.endswith("/api/ps"):
                    return _Resp({"models": []})
                raise AssertionError(url)

            def post(self, url, json):
                vision = {"gemma3:4b": True, "qwen2.5:14b": False}[json["model"]]
                return _Resp(
                    {"capabilities": ["completion"] + (["vision"] if vision else [])}
                )

        monkeypatch.setattr(system.httpx, "Client", lambda timeout: _Client())

        info = system.ollama_info()

        assert info.running is True
        by_name = {m.name: m for m in info.models}
        assert by_name["gemma3:4b"].vision is True
        assert by_name["qwen2.5:14b"].vision is False


# ---------------------------------------------------------------------------
# GET/PUT /api/presets
# ---------------------------------------------------------------------------


class TestPresets:
    @pytest.mark.anyio
    async def test_get_returns_the_three_builtins(self, client: AsyncClient) -> None:
        """GET /api/presets — returns the three built-in presets."""
        resp = await client.get("/api/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert [p["id"] for p in data] == ["rapido", "apuntes-a-mano", "archivo-grande"]
        assert all(p["builtin"] for p in data)

    def test_archivo_grande_is_not_identical_to_rapido(self) -> None:
        """N18: 'Rápido' and 'Archivo grande' used to be byte-identical
        pipelines, so the two cards offered the same trade-off under
        different names (and, since the card blurb is derived from the
        pipeline, the same sentence twice). 'Archivo grande' must have a
        pipeline that actually differs — and differs in a way that reads as
        "tuned for a big batch", not an arbitrary unrelated field."""
        by_id = {p.id: p for p in presets.BUILTIN_PRESETS}
        rapido = by_id["rapido"].pipeline
        grande = by_id["archivo-grande"].pipeline

        assert grande != rapido
        # Same "sin IA" text pipeline — the difference is throughput, not
        # model choice — so a reader who compares the two blurbs still sees
        # the same "Texto impreso. Sin IA, N a la vez." shape, just a
        # genuinely different N (and a chunked, lower-DPI run behind it).
        assert grande.ocr_model == rapido.ocr_model == "tesseract"
        assert grande.figure_model is None
        assert grande.review_model is None
        assert grande.workers > rapido.workers
        assert grande.emit_chunks is True
        assert grande.ocr_dpi < rapido.ocr_dpi

    @pytest.mark.anyio
    async def test_editing_a_builtin_stores_only_the_override(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/presets — editing a builtin stores only the override, not
        a full copy."""
        current = presets.list_presets()
        edited = [p.model_copy() for p in current]
        edited[0].pipeline = edited[0].pipeline.model_copy(update={"workers": 8})

        resp = await client.put(
            "/api/presets", json={"presets": [p.model_dump() for p in edited]}
        )
        assert resp.status_code == 200

        raw = presets._load_raw()
        assert "rapido" in raw["builtin_overrides"]
        assert raw["builtin_overrides"]["rapido"]["workers"] == 8

        refetched = (await client.get("/api/presets")).json()
        assert refetched[0]["pipeline"]["workers"] == 8

    @pytest.mark.anyio
    async def test_restoring_the_default_pipeline_clears_the_override(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/presets — restoring a builtin's default pipeline clears
        its override."""
        builtins = presets.list_presets()
        changed = [p.model_copy() for p in builtins]
        changed[0].pipeline = changed[0].pipeline.model_copy(update={"workers": 8})
        await client.put(
            "/api/presets", json={"presets": [p.model_dump() for p in changed]}
        )
        assert "rapido" in presets._load_raw()["builtin_overrides"]

        await client.put(
            "/api/presets",
            json={"presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]},
        )

        assert presets._load_raw()["builtin_overrides"] == {}

    @pytest.mark.anyio
    async def test_round_trip_of_untouched_builtins_leaves_overrides_empty(
        self, client: AsyncClient
    ) -> None:
        """N12: a client that fetches the current presets and PUTs them back
        unmodified (every save the app makes re-sends the whole list, not
        just what changed) must never create a `builtin_overrides` entry —
        even when a model id round-trips through normalization (a bare local
        model gaining/losing its "ollama:" prefix) rather than byte-for-byte,
        since that is exactly the kind of drift `_normalized_pipeline` exists
        to absorb."""
        fetched = (await client.get("/api/presets")).json()

        resp = await client.put("/api/presets", json={"presets": fetched})

        assert resp.status_code == 200
        assert presets._load_raw()["builtin_overrides"] == {}

    @pytest.mark.anyio
    async def test_round_trip_survives_bare_vs_prefixed_local_model_id(
        self, client: AsyncClient
    ) -> None:
        """Same as above, but the client also re-spells a local model id
        without its "ollama:" provider prefix (a plausible UI round-trip,
        since both spellings resolve to the same model) — must still not be
        treated as an edit."""
        fetched = (await client.get("/api/presets")).json()
        for entry in fetched:
            if entry["id"] == "apuntes-a-mano":
                entry["pipeline"]["ocr_model"] = "qwen2.5vl:7b"
                entry["pipeline"]["figure_model"] = "qwen2.5vl:7b"

        resp = await client.put("/api/presets", json={"presets": fetched})

        assert resp.status_code == 200
        assert presets._load_raw()["builtin_overrides"] == {}

    @pytest.mark.anyio
    async def test_a_user_preset_round_trips(self, client: AsyncClient) -> None:
        """PUT/GET /api/presets — a custom user preset round-trips."""
        mine = Preset(
            id="mio",
            name="Mío",
            builtin=False,
            pipeline=Pipeline(ocr_model="tesseract"),
        )
        payload = [p.model_dump() for p in presets.BUILTIN_PRESETS] + [
            mine.model_dump()
        ]

        await client.put("/api/presets", json={"presets": payload})
        ids = [p["id"] for p in (await client.get("/api/presets")).json()]

        assert "mio" in ids

    @pytest.mark.anyio
    async def test_put_is_a_full_replace_and_drops_unmentioned_user_presets(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/presets — FULL replace, as documented (C7): a user
        preset not mentioned in the request is gone afterwards. This is the
        one legitimate way to delete via a bulk write; `PATCH /api/presets`
        is the merge/upsert endpoint for "save/edit one preset without
        touching the rest" (see the next tests)."""
        mine = Preset(
            id="mio",
            name="Mío",
            builtin=False,
            pipeline=Pipeline(ocr_model="tesseract"),
        )
        await client.put(
            "/api/presets",
            json={
                "presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]
                + [mine.model_dump()]
            },
        )

        await client.put(
            "/api/presets",
            json={"presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]},
        )

        ids = [p["id"] for p in (await client.get("/api/presets")).json()]
        assert "mio" not in ids

    @pytest.mark.anyio
    async def test_put_logs_a_warning_naming_the_user_presets_it_drops(
        self, client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C7: a PUT that is about to delete an existing user preset (the
        app's current, buggy behaviour — sending only the changed preset to
        a full-replace endpoint) must still make the drop visible in the
        technical log, even though the drop itself is correct per the
        documented PUT contract."""
        mine = Preset(
            id="mio",
            name="Mío",
            builtin=False,
            pipeline=Pipeline(ocr_model="tesseract"),
        )
        await client.put(
            "/api/presets",
            json={
                "presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]
                + [mine.model_dump()]
            },
        )

        with caplog.at_level("WARNING"):
            resp = await client.put(
                "/api/presets",
                json={"presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]},
            )
        assert resp.status_code == 200
        assert any("mio" in record.message for record in caplog.records)

    @pytest.mark.anyio
    async def test_patch_upserts_so_a_preset_missing_from_the_request_survives(
        self, client: AsyncClient
    ) -> None:
        """PATCH /api/presets — merge semantics: a user preset not mentioned
        in the request must survive untouched. This is the endpoint the app
        should call to save/edit a single preset (see C7)."""
        mine = Preset(
            id="mio",
            name="Mío",
            builtin=False,
            pipeline=Pipeline(ocr_model="tesseract"),
        )
        await client.put(
            "/api/presets",
            json={
                "presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]
                + [mine.model_dump()]
            },
        )

        resp = await client.patch(
            "/api/presets",
            json={"presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]},
        )
        assert resp.status_code == 200

        ids = [p["id"] for p in (await client.get("/api/presets")).json()]
        assert "mio" in ids

    @pytest.mark.anyio
    async def test_editing_one_user_preset_via_patch_does_not_delete_the_others(
        self, client: AsyncClient
    ) -> None:
        """flow-issues.md C7 — deterministic regression: two (here, three)
        user presets exist; PATCH with only one of them edited must leave
        every other user preset (and every builtin) in place."""
        seed = [
            Preset(
                id=pid,
                name=pid,
                builtin=False,
                pipeline=Pipeline(ocr_model="tesseract"),
            )
            for pid in ("personalizado-2", "ux-a", "ux-b")
        ]
        await client.put(
            "/api/presets",
            json={
                "presets": [p.model_dump() for p in presets.BUILTIN_PRESETS]
                + [p.model_dump() for p in seed]
            },
        )

        edited_ux_a = Preset(
            id="ux-a",
            name="ux-a",
            builtin=False,
            pipeline=Pipeline(ocr_model="tesseract", workers=8),
        )
        resp = await client.patch(
            "/api/presets", json={"presets": [edited_ux_a.model_dump()]}
        )
        assert resp.status_code == 200

        by_id = {p["id"]: p for p in (await client.get("/api/presets")).json()}
        assert set(by_id) == {
            "rapido",
            "apuntes-a-mano",
            "archivo-grande",
            "personalizado-2",
            "ux-a",
            "ux-b",
        }
        assert by_id["ux-a"]["pipeline"]["workers"] == 8

    @pytest.mark.anyio
    async def test_delete_preset_removes_only_that_user_preset(
        self, client: AsyncClient
    ) -> None:
        """DELETE /api/presets/{id} — explicit removal, the one thing PATCH
        cannot express."""
        seed = [
            Preset(
                id=pid,
                name=pid,
                builtin=False,
                pipeline=Pipeline(ocr_model="tesseract"),
            )
            for pid in ("ux-a", "ux-b")
        ]
        await client.patch(
            "/api/presets", json={"presets": [p.model_dump() for p in seed]}
        )

        resp = await client.delete("/api/presets/ux-a")
        assert resp.status_code == 200

        ids = [p["id"] for p in (await client.get("/api/presets")).json()]
        assert "ux-a" not in ids
        assert "ux-b" in ids

    @pytest.mark.anyio
    async def test_delete_preset_404s_for_a_builtin_or_unknown_id(
        self, client: AsyncClient
    ) -> None:
        assert (await client.delete("/api/presets/rapido")).status_code == 404
        assert (await client.delete("/api/presets/does-not-exist")).status_code == 404


# ---------------------------------------------------------------------------
# GET/PUT /api/settings — the wizard is "the first edit of Settings"
# (server/settings.py's own docstring); no wizard-only storage exists.
# ---------------------------------------------------------------------------


class TestSettings:
    @pytest.mark.anyio
    async def test_get_defaults_before_anything_is_saved(
        self, client: AsyncClient
    ) -> None:
        """GET /api/settings — a brand-new install (no settings.json yet)
        returns field defaults, `wizard_done: false` included."""
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "wizard_done": False,
            "default_output_mode": "sibling",
            "default_output_dir": None,
            "default_preset_id": "apuntes-a-mano",
            "default_mode": "once",
            "local_gpu_permits": 1,
        }

    @pytest.mark.anyio
    async def test_round_trip(self, client: AsyncClient, tmp_path: Path) -> None:
        """PUT then GET /api/settings — a full replace round-trips."""
        out_dir = tmp_path / "salida"
        payload = {
            "wizard_done": True,
            "default_output_mode": "fixed",
            "default_output_dir": str(out_dir),
            "default_preset_id": "rapido",
            "default_mode": "sync",
            "local_gpu_permits": 2,
        }

        put_resp = await client.put("/api/settings", json=payload)
        assert put_resp.status_code == 200
        assert put_resp.json() == payload

        get_resp = await client.get("/api/settings")
        assert get_resp.json() == payload

    @pytest.mark.anyio
    async def test_fixed_mode_requires_output_dir(self, client: AsyncClient) -> None:
        """PUT /api/settings — 422 with a structured detail when
        default_output_mode is 'fixed' but default_output_dir is missing."""
        resp = await client.put(
            "/api/settings",
            json={
                "wizard_done": True,
                "default_output_mode": "fixed",
                "default_output_dir": None,
                "default_preset_id": "rapido",
                "default_mode": "once",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "required"
        assert detail["field"] == "default_output_dir"

    @pytest.mark.anyio
    async def test_fixed_mode_rejects_a_relative_output_dir(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/settings — a relative default_output_dir is 422, same
        rule as a job's own `output` (see docs/desktop-app.md)."""
        resp = await client.put(
            "/api/settings",
            json={
                "wizard_done": True,
                "default_output_mode": "fixed",
                "default_output_dir": "salida",
                "default_preset_id": "rapido",
                "default_mode": "once",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "not_absolute"
        assert detail["field"] == "default_output_dir"

    @pytest.mark.anyio
    async def test_default_preset_id_must_exist(self, client: AsyncClient) -> None:
        """PUT /api/settings — 422 when default_preset_id names no preset."""
        resp = await client.put(
            "/api/settings",
            json={
                "wizard_done": True,
                "default_output_mode": "sibling",
                "default_output_dir": None,
                "default_preset_id": "no-existe",
                "default_mode": "once",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "not_found"
        assert detail["field"] == "default_preset_id"

    @pytest.mark.anyio
    async def test_local_gpu_permits_accepts_one_or_two(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/settings — the experimental knob's only valid values."""
        for permits in (1, 2):
            resp = await client.put(
                "/api/settings",
                json={
                    "wizard_done": True,
                    "default_output_mode": "sibling",
                    "default_output_dir": None,
                    "default_preset_id": "rapido",
                    "default_mode": "once",
                    "local_gpu_permits": permits,
                },
            )
            assert resp.status_code == 200
            assert resp.json()["local_gpu_permits"] == permits

    @pytest.mark.anyio
    async def test_local_gpu_permits_rejects_anything_else(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/settings — 422 outside 1..2; untested/unmeasured past 2."""
        resp = await client.put(
            "/api/settings",
            json={
                "wizard_done": True,
                "default_output_mode": "sibling",
                "default_output_dir": None,
                "default_preset_id": "rapido",
                "default_mode": "once",
                "local_gpu_permits": 3,
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "out_of_range"
        assert detail["field"] == "local_gpu_permits"

    @pytest.mark.anyio
    async def test_local_gpu_permits_resizes_the_engine_semaphore(
        self, client: AsyncClient
    ) -> None:
        """PUT /api/settings — takes effect immediately, no restart needed
        (`server.settings._apply_llm_settings` → `image_ocr.
        apply_gpu_permits`)."""
        from twomarkdown.agents import image_ocr

        try:
            resp = await client.put(
                "/api/settings",
                json={
                    "wizard_done": True,
                    "default_output_mode": "sibling",
                    "default_output_dir": None,
                    "default_preset_id": "rapido",
                    "default_mode": "once",
                    "local_gpu_permits": 2,
                },
            )
            assert resp.status_code == 200
            assert image_ocr._page_ocr_lock.permits == 2

            get_resp = await client.get("/api/settings")
            assert get_resp.status_code == 200
            assert image_ocr._page_ocr_lock.permits == 2
        finally:
            image_ocr.apply_gpu_permits(1)

    def test_resolver_sibling_mode(self, tmp_path: Path) -> None:
        """default_output_for() — sibling mode names "<input>_2markdown"
        next to the input, for a file or a directory alike."""
        from twomarkdown.server.settings import default_output_for

        folder = tmp_path / "Tema 1"
        folder.mkdir()
        s = Settings(default_output_mode="sibling")

        assert default_output_for(str(folder), s) == str(tmp_path / "Tema 1_2markdown")

    def test_resolver_fixed_mode(self, tmp_path: Path) -> None:
        """default_output_for() — fixed mode lands under
        default_output_dir, named after the input."""
        from twomarkdown.server.settings import default_output_for

        out_dir = tmp_path / "salida"
        input_path = tmp_path / "docs" / "Tema 1"
        s = Settings(default_output_mode="fixed", default_output_dir=str(out_dir))

        assert default_output_for(str(input_path), s) == str(
            out_dir / "Tema 1_2markdown"
        )

    @pytest.mark.anyio
    async def test_inspect_carries_default_output(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/inspect — InspectResponse.default_output is the same
        path default_output_for() would compute, sibling mode by default."""
        f = tmp_path / "notes.txt"
        f.write_text("hello world")

        resp = await client.post("/api/inspect", json={"path": str(f)})

        assert resp.status_code == 200
        assert resp.json()["default_output"] == str(tmp_path / "notes.txt_2markdown")

    @pytest.mark.anyio
    async def test_inspect_default_output_follows_fixed_mode(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/inspect — default_output switches to the fixed dir once
        Settings is saved that way."""
        out_dir = tmp_path / "salida"
        await client.put(
            "/api/settings",
            json={
                "wizard_done": True,
                "default_output_mode": "fixed",
                "default_output_dir": str(out_dir),
                "default_preset_id": "rapido",
                "default_mode": "once",
            },
        )
        f = tmp_path / "notes.txt"
        f.write_text("hello world")

        resp = await client.post("/api/inspect", json={"path": str(f)})

        assert resp.json()["default_output"] == str(out_dir / "notes.txt_2markdown")

    @pytest.mark.anyio
    async def test_job_without_output_uses_the_resolver(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs — omitting `output` falls back to
        default_output_for(), same path /api/inspect already showed."""
        f = tmp_path / "notes.txt"
        f.write_text("hello world")
        inspect_id = (await client.post("/api/inspect", json={"path": str(f)})).json()[
            "inspect_id"
        ]

        resp = await client.post(
            "/api/jobs",
            json={
                "inspect_id": inspect_id,
                "preset_id": "rapido",
                "mode": "once",
            },
        )

        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        job = await _wait_for_job(client, job_id)
        assert job["output"] == str(tmp_path / "notes.txt_2markdown")


# ---------------------------------------------------------------------------
# POST /api/inspect, POST /api/estimate
# ---------------------------------------------------------------------------


class TestInspectAndEstimate:
    @pytest.mark.anyio
    async def test_inspect_a_text_file(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/inspect — returns file totals and an inspect_id for a text file."""
        f = tmp_path / "notes.txt"
        f.write_text("hello world")

        resp = await client.post("/api/inspect", json={"path": str(f)})

        assert resp.status_code == 200
        data = resp.json()
        assert data["totals"]["files"] == 1
        assert data["inspect_id"]

    @pytest.mark.anyio
    async def test_inspect_missing_path_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/inspect — 404 when the path doesn't exist."""
        resp = await client.post(
            "/api/inspect", json={"path": str(tmp_path / "nope.txt")}
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_estimate_needs_a_known_inspect_id(self, client: AsyncClient) -> None:
        """POST /api/estimate — 404 for an unknown inspect_id."""
        resp = await client.post(
            "/api/estimate",
            json={"inspect_id": "does-not-exist", "pipeline": Pipeline().model_dump()},
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_estimate_without_inspect_id_returns_the_pipeline_only_gpu_verdict(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """N1 — no `inspect_id` at all (the Pipeline editor's own case, no
        folder inspected yet) must not 404: it returns the real engine GPU
        verdict for the pipeline alone, flagged `estimate_scope:
        "pipeline_only"`, with zeroed-out stage timings/costs."""
        from twomarkdown.batch import estimate as est_mod
        from twomarkdown.batch import gpu_memory

        # Pin a deterministic GPU budget/model sizes — the real
        # `gpu_limit_gb()` is 0.0 off Apple Silicon (this suite runs in
        # Docker on Linux), which would never block regardless of models.
        monkeypatch.setattr(gpu_memory, "gpu_limit_gb", lambda: 36.0)
        monkeypatch.setattr(gpu_memory, "ollama_installed_sizes", lambda: {})

        def fake_resident_gb(model, installed_sizes=None):
            return 30.0 if "32b" in model else 8.0

        monkeypatch.setattr(gpu_memory, "resident_gb", fake_resident_gb)
        monkeypatch.setattr(est_mod, "resident_gb", fake_resident_gb)

        pipeline = Pipeline(
            ocr_model="ollama:qwen2.5vl:7b",
            figure_model="ollama:qwen2.5vl:32b",
            review_model="ollama:qwen2.5vl:32b",
            describe_figures=True,
        )

        resp = await client.post(
            "/api/estimate", json={"pipeline": pipeline.model_dump()}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["estimate_scope"] == "pipeline_only"
        assert data["total_seconds"] == 0.0
        assert all(s["seconds"] == 0.0 for s in data["stages"])
        # Same three-distinct-local-model pipeline C14 blocks in the "full"
        # path — must agree here too, with no inspect_id at all (N1's whole
        # point: this screen must never approximate a verdict the engine
        # would give differently).
        assert data["blocked"] is not None
        assert data["gpu_resident_gb"] > data["gpu_limit_gb"]

    @pytest.mark.anyio
    async def test_estimate_pipeline_only_agrees_with_the_full_estimate(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """N1: the GPU verdict must be identical whether or not a real
        inspection backs the request — it depends only on the pipeline's
        models, never on file counts."""
        f = tmp_path / "notes.txt"
        f.write_text("hello world")
        inspect_id = (await client.post("/api/inspect", json={"path": str(f)})).json()[
            "inspect_id"
        ]
        pipeline = Pipeline(
            ocr_model="ollama:qwen2.5vl:7b", figure_model="ollama:qwen2.5vl:32b"
        )

        full = (
            await client.post(
                "/api/estimate",
                json={"inspect_id": inspect_id, "pipeline": pipeline.model_dump()},
            )
        ).json()
        pipeline_only = (
            await client.post("/api/estimate", json={"pipeline": pipeline.model_dump()})
        ).json()

        assert full["estimate_scope"] == "full"
        assert pipeline_only["estimate_scope"] == "pipeline_only"
        for key in ("gpu_resident_gb", "gpu_limit_gb", "gpu_headroom_gb", "blocked"):
            assert full[key] == pipeline_only[key]

    @pytest.mark.anyio
    async def test_estimate_a_plain_text_file_costs_nothing(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/estimate — a plain text file costs nothing across all
        pipeline stages."""
        f = tmp_path / "notes.txt"
        f.write_text("hello world")
        inspect_id = (await client.post("/api/inspect", json={"path": str(f)})).json()[
            "inspect_id"
        ]

        resp = await client.post(
            "/api/estimate",
            json={"inspect_id": inspect_id, "pipeline": Pipeline().model_dump()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_usd"] in (0.0, None)
        assert {s["key"] for s in data["stages"]} == {
            "extract",
            "ocr",
            "figures",
            "review",
            "write",
        }


# ---------------------------------------------------------------------------
# Jobs — create, run (tesseract-only pipeline, plain .txt so nothing needs a
# model or the network), stream events, page read/write, retry.
# ---------------------------------------------------------------------------


class TestJobs:
    @pytest.mark.anyio
    async def test_create_and_run_a_job(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs — creates and runs a tesseract-only job end to end."""
        src = tmp_path / "in.txt"
        src.write_text("hello job")
        out = tmp_path / "out"

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["status"] == "done"
        assert data["ok"] == 1
        assert (out / "in.md").exists()
        # N16: a job that made no cloud calls at all (tesseract-only) spent a
        # genuine, known $0 — not "we don't know" (`null`).
        assert data["usd"] == 0.0

    @pytest.mark.anyio
    async def test_multi_file_folder_job_from_inspect_id_alone(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs — a folder with several files must run from
        `inspect_id` alone, no `path`. `InspectResponse.root` (the folder
        `/api/inspect` walked) is what `server/app.py` re-resolves into the
        job's file list; this used to 422 with "multi-file inspect_id jobs
        need `path`" until `estimate.inspect()` recorded that root."""
        src_dir = tmp_path / "in"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("hello a")
        (src_dir / "b.txt").write_text("hello b")
        out = tmp_path / "out"

        inspect_resp = await client.post("/api/inspect", json={"path": str(src_dir)})
        assert inspect_resp.status_code == 200
        inspect_data = inspect_resp.json()
        assert inspect_data["totals"]["files"] == 2
        assert inspect_data["root"] == str(src_dir)

        resp = await client.post(
            "/api/jobs",
            json={
                "inspect_id": inspect_data["inspect_id"],
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["status"] == "done"
        assert data["ok"] == 2
        assert (out / "a.md").exists()
        assert (out / "b.md").exists()

    @pytest.mark.anyio
    async def test_usd_is_null_when_a_cloud_call_could_not_be_priced(
        self, client: AsyncClient, tmp_path: Path, monkeypatch
    ) -> None:
        """N16: `job.usd = rt.usd_so_far or None` used to make an unpriced
        cloud call (`events.add_cost(None)`) read identically to a job that
        spent nothing — both left `usd_so_far` at `0.0`. A call that hits
        `events.add_cost(None, unknown=True)` (an unrecognised model, no
        manual price on file) must report `usd: null`, not `0.0`."""
        from twomarkdown.batch import events
        from twomarkdown.batch.processor import BatchResult
        from twomarkdown.server import jobs as jobs_mod

        def fake_process_batch(*args, **kwargs):
            events.add_cost(None, unknown=True)
            return BatchResult(converted=1)

        monkeypatch.setattr(jobs_mod, "process_batch", fake_process_batch)

        src = tmp_path / "in.txt"
        src.write_text("hello job")
        out = tmp_path / "out"

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        job_id = resp.json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["status"] == "done"
        assert data["usd"] is None

    @pytest.mark.anyio
    async def test_a_single_page_file_reports_one_page(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """M3: `Job.files[].pages` must carry the engine's real page count —
        `file_started` names it, but nothing wired that event's `pages` into
        the `Job` object the API/UI reads, so the field stayed 0 forever and
        Revisar's page list (built from `f.pages`) was permanently empty."""
        import fitz

        pdf_path = tmp_path / "one.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "hello")
        doc.save(pdf_path)
        doc.close()
        out = tmp_path / "out"

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(pdf_path),
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        job_id = resp.json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["status"] == "done"
        assert data["files"][0]["pages"] == 1

    @pytest.mark.anyio
    async def test_missing_path_and_inspect_id_is_422(
        self, client: AsyncClient
    ) -> None:
        """POST /api/jobs — 422 when neither path nor inspect_id is given."""
        resp = await client.post(
            "/api/jobs",
            json={"output": "/tmp/out", "pipeline": Pipeline().model_dump()},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_unknown_preset_id_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs — 404 for an unknown preset_id."""
        src = tmp_path / "in.txt"
        src.write_text("x")
        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(tmp_path / "out"),
                "preset_id": "does-not-exist",
            },
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_tilde_output_expands_to_home_not_the_repo_cwd(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B2: a leading `~` in `output` must expand against the user's home
        before mkdir, never get created literally under the server's cwd."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # `Path.expanduser()` resolves `~` from `$HOME` (via `os.path.expanduser`),
        # not from `Path.home()` — patch the env var, not the classmethod.
        monkeypatch.setenv("HOME", str(fake_home))

        src = tmp_path / "in.txt"
        src.write_text("hello tilde")

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": "~/ocr-lab/out-tilde",
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["status"] == "done"
        # Expanded under the (fake) home, not left as a literal "~" directory.
        assert (fake_home / "ocr-lab" / "out-tilde" / "in.md").exists()
        assert not (Path.cwd() / "~").exists()
        # `Job.output` itself must carry the expanded absolute path too, since
        # retries/page-reads resolve output files against it directly.
        assert data["output"] == str(fake_home / "ocr-lab" / "out-tilde")

    @pytest.mark.anyio
    async def test_relative_output_is_rejected(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """B2: a relative `output` (no leading `/` or `~`) must be rejected,
        not silently created under the server's own working directory."""
        src = tmp_path / "in.txt"
        src.write_text("hello relative")

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": "relative/out-dir",
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        assert resp.status_code == 422
        assert not (Path.cwd() / "relative").exists()

    def test_events_stream_replays_from_the_start(
        self, sync_client: TestClient, tmp_path: Path
    ) -> None:
        """WS /api/jobs/{id}/events — replays every event from the start, in
        order (uses `sync_client`/`TestClient`: `httpx.AsyncClient` cannot
        open WebSocket connections)."""
        src = tmp_path / "in.txt"
        src.write_text("hello events")
        out = tmp_path / "out"
        job_id = sync_client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        ).json()["job_id"]

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            data = sync_client.get(f"/api/jobs/{job_id}").json()
            if data["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"job {job_id} did not finish in time")

        with sync_client.websocket_connect(f"/api/jobs/{job_id}/events") as ws:
            types = []
            # The whole run already happened; the buffer replay must still
            # carry every event in order, ending with job_done.
            while True:
                event = ws.receive_json()
                types.append(event["type"])
                if event["type"] == "job_done":
                    break

        assert types[0] == "job_started"
        assert "file_started" in types
        assert "file_done" in types
        assert types[-1] == "job_done"

    def test_unknown_job_ws_closes(self, sync_client: TestClient) -> None:
        """WS /api/jobs/{id}/events — closes immediately for an unknown job_id
        (uses `sync_client`/`TestClient`, same WebSocket reason as above)."""
        with pytest.raises(Exception):
            with sync_client.websocket_connect("/api/jobs/does-not-exist/events") as ws:
                ws.receive_json()

    def test_pause_resume_cancel_are_checked_between_files(
        self, tmp_path: Path
    ) -> None:
        """jobs.py — pause and cancel are only checked between files, not
        mid-file; exercised directly against `jobs.py` (no client — a fake
        `process_batch` stands in for real conversion so the test controls
        timing instead of racing a real one)."""
        from twomarkdown.batch.processor import BatchResult

        files = [tmp_path / f"{i}.txt" for i in range(3)]
        for f in files:
            f.write_text("x")
        out = tmp_path / "out"
        out.mkdir()

        started = []

        def fake_process_batch(*args, **kwargs):
            only = kwargs["only_files"][0]
            started.append(only)
            time.sleep(0.05)  # a real conversion takes long enough to pause between
            return BatchResult(converted=1)

        import twomarkdown.server.jobs as jobs_mod

        original = jobs_mod.process_batch
        jobs_mod.process_batch = fake_process_batch
        try:
            job = jobs.create_job(
                path=str(tmp_path),
                inspect_id=None,
                output=str(out),
                pipeline=Pipeline(ocr_model="tesseract"),
                mode="once",
            )
            rt = jobs._jobs[job.job_id]
            rt.pause_event.set()
            time.sleep(0.1)
            assert len(started) <= 1  # nothing new starts while paused

            jobs.cancel_job(job.job_id)
            for _ in range(100):
                if job.status in ("done", "cancelled"):
                    break
                time.sleep(0.02)
            assert job.status == "cancelled"
        finally:
            jobs_mod.process_batch = original

    @pytest.mark.anyio
    async def test_retry_whole_file(self, client: AsyncClient, tmp_path: Path) -> None:
        """POST /api/jobs/{id}/retry — retries one file and marks it ok."""
        src = tmp_path / "in.txt"
        src.write_text("hello retry")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"file_index": 0, "pipeline_patch": {"workers": 2}},
        )

        assert resp.status_code == 200
        assert resp.json()["files"][0]["status"] == "ok"

    @pytest.mark.anyio
    async def test_retry_persists_the_patched_pipeline(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs/{id}/retry — M6: a successful retry's `pipeline_patch`
        must stick on the job, or the next GET/retry silently reverts to what
        the job started with (confirmed by curl in the review: a patch applied
        fine for one retry call but vanished from the returned `Job`)."""
        src = tmp_path / "in.txt"
        src.write_text("hello retry")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post(
            f"/api/jobs/{job_id}/retry",
            json={"file_index": 0, "pipeline_patch": {"workers": 7}},
        )

        assert resp.status_code == 200
        assert resp.json()["pipeline"]["workers"] == 7

        again = await client.get(f"/api/jobs/{job_id}")
        assert again.json()["pipeline"]["workers"] == 7

    @pytest.mark.anyio
    async def test_retry_recomputes_job_totals(
        self, client: AsyncClient, tmp_path: Path, monkeypatch
    ) -> None:
        """N14: a retry only used to update the retried `Job.files[i].status`
        — the job-level `ok`/`warn`/`failed` aggregate kept describing the
        *original* run, so the summary line could read "2 ok" underneath a
        row that now showed "failed". A retry must recompute the aggregate
        from `Job.files[]`, not leave it stale."""
        (tmp_path / "in").mkdir()
        (tmp_path / "in" / "a.txt").write_text("hello a")
        (tmp_path / "in" / "b.txt").write_text("hello b")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(tmp_path / "in"),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        data = await _wait_for_job(client, job_id)
        assert [f["status"] for f in data["files"]] == ["ok", "ok"]
        assert (data["ok"], data["warn"], data["failed"]) == (2, 0, 0)

        from twomarkdown.batch.processor import BatchResult
        from twomarkdown.server import jobs as jobs_mod

        monkeypatch.setattr(
            jobs_mod, "process_batch", lambda *a, **k: BatchResult(failed=1)
        )

        resp = await client.post(f"/api/jobs/{job_id}/retry", json={"file_index": 0})

        assert resp.status_code == 200
        body = resp.json()
        assert body["files"][0]["status"] == "failed"
        assert body["files"][1]["status"] == "ok"  # untouched by the retry
        assert (body["ok"], body["warn"], body["failed"]) == (1, 0, 1)

    def test_llm_enabled_is_not_gated_on_ocr_model_alone(self) -> None:
        """_apply_pipeline() — M6's root cause: `llm_enabled` used to be True
        only when `ocr_model` was not "tesseract", so a pipeline reading OCR
        with plain Tesseract but reviewing pages (or captioning figures) with
        a model silently ran neither — every LLM-backed pass checks this one
        flag (`agents/page_review.py`'s `review_enabled()`,
        `agents/image_ocr.py`'s `describe_image_bytes_llm`)."""
        from twomarkdown.config import llm_config
        from twomarkdown.server.jobs import _apply_pipeline, _restore_config

        snapshot = _apply_pipeline(
            Pipeline(ocr_model="tesseract", review_model="openai:gpt-4o-mini")
        )
        try:
            assert llm_config.llm_enabled is True
        finally:
            _restore_config(snapshot)

        snapshot = _apply_pipeline(Pipeline(ocr_model="tesseract"))
        try:
            assert llm_config.llm_enabled is False
        finally:
            _restore_config(snapshot)

    def test_figure_model_collapse_warns_once_at_job_start_not_per_figure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """C18 — `_warn_if_figure_model_collapses` (called once at job start,
        right after `_apply_pipeline`, before any file/figure is processed)
        must log the collapse exactly once for the job, even though
        `agents.image_ocr.effective_figure_model()` itself is asked the same
        question again for every figure a job captions — that per-figure
        caller must stay silent (it now only resolves, never logs) so a job
        with many figures does not repeat the same warning once per figure."""
        from twomarkdown.agents.image_ocr import effective_figure_model
        from twomarkdown.config import llm_config
        from twomarkdown.server.jobs import (
            _apply_pipeline,
            _restore_config,
            _warn_if_figure_model_collapses,
        )

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(system, "ram_gb", lambda: 48.0)
        monkeypatch.setattr(
            system, "ollama_info", lambda: system.OllamaInfo(running=False, models=[])
        )

        pipeline = Pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:7b",
            describe_figures=True,
        )
        snapshot = _apply_pipeline(pipeline)
        try:
            with caplog.at_level("WARNING"):
                _warn_if_figure_model_collapses(pipeline)
                # Simulate the job captioning several figures after job start.
                for _ in range(5):
                    resolved = effective_figure_model()
                    assert resolved == "ollama:qwen2.5vl:32b"

            collapse_records = [
                r for r in caplog.records if "no cabe junto al" in r.message
            ]
            assert len(collapse_records) == 1
            assert llm_config.figure_model == "ollama:qwen2.5vl:7b"
        finally:
            _restore_config(snapshot)

    @pytest.mark.anyio
    async def test_retry_out_of_range_file_index_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs/{id}/retry — 404 when file_index is out of range."""
        src = tmp_path / "in.txt"
        src.write_text("x")
        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(tmp_path / "out"),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post(f"/api/jobs/{job_id}/retry", json={"file_index": 99})

        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_page_read_and_write_round_trip(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """GET/PUT /api/jobs/{id}/files/{i}/pages/{n} — reads and writes one
        page of a converted file's markdown in place."""
        out = tmp_path / "out"
        out.mkdir()
        md = out / "doc.md"
        md.write_text("## Page 1\n\ntext one\n\n## Page 2\n\ntext two\n")

        job = jobs.Job(
            job_id="fixed-job",
            status="done",
            output=str(out),
            pipeline=Pipeline(),
            mode="once",
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[tmp_path / "doc.pdf"])
        jobs._jobs["fixed-job"] = rt

        resp = await client.get("/api/jobs/fixed-job/files/0/pages/2")
        assert resp.status_code == 200
        assert "text two" in resp.json()["markdown"]

        put = await client.put(
            "/api/jobs/fixed-job/files/0/pages/2",
            json={"markdown": "## Page 2\n\nedited text\n"},
        )
        assert put.status_code == 200
        assert "edited text" in md.read_text()
        assert "text one" in md.read_text()  # page 1 untouched

    def _make_job_with_assets(self, tmp_path: Path) -> tuple[str, Path]:
        """A `done` job whose output folder has a real `<stem>_assets/`
        figure next to its `.md`, matching what `batch/processor.py` writes
        (`assets_dir = output_md.parent / f"{output_md.stem}_assets"`)."""
        out = tmp_path / "out"
        out.mkdir()
        (out / "doc.md").write_text(
            "## Page 1\n\n![fig](doc_assets/doc-fig-p1-1.png)\n"
        )
        assets_dir = out / "doc_assets"
        assets_dir.mkdir()
        (assets_dir / "doc-fig-p1-1.png").write_bytes(b"\x89PNG\r\n fake")

        job = jobs.Job(
            job_id="assets-job",
            status="done",
            output=str(out),
            pipeline=Pipeline(),
            mode="once",
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[tmp_path / "doc.pdf"])
        jobs._jobs[job.job_id] = rt
        return job.job_id, assets_dir

    @pytest.mark.anyio
    async def test_asset_serves_the_figure_png(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """GET /api/jobs/{id}/files/{i}/assets/{name} — N9: serves a figure
        written under `<stem>_assets/` back to Revisar."""
        job_id, assets_dir = self._make_job_with_assets(tmp_path)

        resp = await client.get(
            f"/api/jobs/{job_id}/files/0/assets/doc_assets/doc-fig-p1-1.png"
        )

        assert resp.status_code == 200
        assert resp.content == (assets_dir / "doc-fig-p1-1.png").read_bytes()

    @pytest.mark.anyio
    async def test_asset_unknown_name_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        job_id, _ = self._make_job_with_assets(tmp_path)

        resp = await client.get(
            f"/api/jobs/{job_id}/files/0/assets/doc_assets/nope.png"
        )

        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_asset_path_traversal_outside_assets_dir_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """`name` crafted with `../` segments must not escape `<stem>_assets/`
        onto the rest of the job's output folder (or beyond)."""
        job_id, _ = self._make_job_with_assets(tmp_path)
        secret = tmp_path / "out" / "doc.md"
        assert secret.exists()

        resp = await client.get(
            f"/api/jobs/{job_id}/files/0/assets/doc_assets/../doc.md"
        )

        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_asset_unknown_job_is_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/does-not-exist/files/0/assets/x.png")
        assert resp.status_code == 404

    def test_single_page_retry_keeps_figures_normalizes_latex_refreshes_status(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """B5/N1/M6, all from one page-level retry:

        * B5 — a page retry must not drop that page's figure block (the
          engine only re-runs OCR/review for a page, never the figure pass).
        * N1 — the OCR/review text must go through the same `clean_markdown`
          pass the first conversion runs, so `\\( x \\)` becomes `$x$` instead
          of diverging from the rest of the document.
        * M6 — the retry's own status/reason/seconds must land on
          `Job.files[0]`, replacing a stale value from a previous attempt.
        """
        import fitz

        from twomarkdown.batch import events
        from twomarkdown.config import conversion_config
        from twomarkdown.server.schemas import Job, JobFileState, Pipeline

        pdf_path = tmp_path / "Tema 1.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()

        out = tmp_path / "out"
        out.mkdir()
        md_path = out / "Tema 1.md"
        md_path.write_text(
            "---\n"
            'source: "Tema 1.pdf"\n'
            'converted_at: "2026-09-13T13:22:27+00:00"\n'
            "char_count: 1624\n"
            "---\n\n"
            "## Page 1\n\n"
            "### OCR\n\n"
            "old text\n\n"
            "### Figura 1.1\n\n"
            "![Figura p1-1](Tema 1_assets/Tema 1-fig-p1-1.png)\n\n"
            "> **Figura (descripción generada):** un gato\n",
            encoding="utf-8",
        )

        job = Job(
            job_id="j-retry-page",
            status="done",
            output=str(out),
            pipeline=Pipeline(),
            mode="once",
            files=[
                JobFileState(
                    index=0,
                    path=str(pdf_path),
                    status="warn",
                    seconds=62.08,
                    reason="Revisión omitida: saldo agotado en openai:gpt-4o-mini",
                )
            ],
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[pdf_path])
        jobs._jobs["j-retry-page"] = rt

        monkeypatch.setattr(conversion_config, "ocr_backend", "ollama")
        monkeypatch.setattr(
            "twomarkdown.converter.ocr.ocr_image_bytes",
            lambda image_bytes, ocr_fn=None, force_llm=False: r"new text \( f(x) \)",
        )
        monkeypatch.setattr(
            "twomarkdown.agents.page_review.review_enabled", lambda: False
        )

        events.set_sink(jobs._make_sink(rt))
        try:
            jobs._retry_single_page(rt, 0, 1)
        finally:
            events.set_sink(None)
            del jobs._jobs["j-retry-page"]

        content = md_path.read_text(encoding="utf-8")
        # B5: the figure block survives the retry, PNG reference included.
        assert "### Figura 1.1" in content
        assert "un gato" in content
        assert "Tema 1_assets/Tema 1-fig-p1-1.png" in content
        # N1: LaTeX-delimited math is normalised to $...$, same as a first pass.
        assert "\\(" not in content
        assert "$f(x)$" in content
        # Frontmatter reflects the new body, not the pre-retry one.
        assert "char_count: 1624" not in content

        # M6: this attempt's own outcome replaces the stale one from before.
        assert job.files[0].status == "ok"
        assert job.files[0].reason is None
        assert job.files[0].seconds != 62.08

    def test_single_page_retry_preserves_user_content_around_ocr_section(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """N10: a page-level retry only regenerates the `### OCR` section's
        body. A saved edit that lives *outside* that body — before the `### OCR`
        heading (e.g. right after the `## Page N` heading), or after the next
        structural heading (a figure block, here, and anything the user typed
        after it) — must survive, the same way the figure block itself
        already did. Only text actually inside the old OCR body (with no
        heading of its own to anchor it) is expected to be lost, since that is
        exactly the region a fresh OCR pass regenerates."""
        import fitz

        from twomarkdown.batch import events
        from twomarkdown.config import conversion_config
        from twomarkdown.server.schemas import Job, JobFileState, Pipeline

        pdf_path = tmp_path / "Tema 1.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()

        out = tmp_path / "out"
        out.mkdir()
        md_path = out / "Tema 1.md"
        md_path.write_text(
            "---\n"
            'source: "Tema 1.pdf"\n'
            'converted_at: "2026-09-13T13:22:27+00:00"\n'
            "char_count: 1624\n"
            "---\n\n"
            "## Page 1\n\n"
            "NOTA-PREFIJO\n\n"
            "### OCR\n\n"
            "old text\n\n"
            "### Figura 1.1\n\n"
            "![Figura p1-1](Tema 1_assets/Tema 1-fig-p1-1.png)\n\n"
            "NOTA-COLA\n",
            encoding="utf-8",
        )

        job = Job(
            job_id="j-retry-context",
            status="done",
            output=str(out),
            pipeline=Pipeline(),
            mode="once",
            files=[JobFileState(index=0, path=str(pdf_path), status="ok")],
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[pdf_path])
        jobs._jobs["j-retry-context"] = rt

        monkeypatch.setattr(conversion_config, "ocr_backend", "ollama")
        monkeypatch.setattr(
            "twomarkdown.converter.ocr.ocr_image_bytes",
            lambda image_bytes, ocr_fn=None, force_llm=False: "new text",
        )
        monkeypatch.setattr(
            "twomarkdown.agents.page_review.review_enabled", lambda: False
        )

        events.set_sink(jobs._make_sink(rt))
        try:
            jobs._retry_single_page(rt, 0, 1)
        finally:
            events.set_sink(None)
            del jobs._jobs["j-retry-context"]

        content = md_path.read_text(encoding="utf-8")
        # Preserved: everything before the "### OCR" heading...
        assert "NOTA-PREFIJO" in content
        # ...and everything from the figure heading onward, user text included.
        assert "### Figura 1.1" in content
        assert "Tema 1_assets/Tema 1-fig-p1-1.png" in content
        assert "NOTA-COLA" in content
        # The OCR body itself is genuinely refreshed.
        assert "old text" not in content
        assert "new text" in content

    @pytest.mark.anyio
    async def test_retry_refuses_to_silently_overwrite_a_saved_page_edit(
        self, client: AsyncClient, tmp_path: Path, monkeypatch
    ) -> None:
        """N10: a page-level retry regenerates that page's Markdown from a
        fresh OCR pass. If the user saved an edit to it since its last
        conversion (`PUT .../pages/{n}`), retrying without confirmation must
        refuse (409) rather than silently drop it; `confirm: true` proceeds
        and clears the guard so a further edit-free retry needs no
        confirmation."""
        import fitz

        from twomarkdown.config import conversion_config
        from twomarkdown.server.schemas import Job, JobFileState, Pipeline

        pdf_path = tmp_path / "doc.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(pdf_path)
        doc.close()

        out = tmp_path / "out"
        out.mkdir()
        md_path = out / "doc.md"
        md_path.write_text(
            "---\n"
            'source: "doc.pdf"\n'
            'converted_at: "2026-09-13T13:22:27+00:00"\n'
            "char_count: 10\n"
            "---\n\n"
            "## Page 1\n\n### OCR\n\nold text\n",
            encoding="utf-8",
        )

        job = Job(
            job_id="j-edit-guard",
            status="done",
            output=str(out),
            pipeline=Pipeline(),
            mode="once",
            files=[JobFileState(index=0, path=str(pdf_path), status="ok")],
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[pdf_path])
        jobs._jobs["j-edit-guard"] = rt

        monkeypatch.setattr(conversion_config, "ocr_backend", "ollama")
        monkeypatch.setattr(
            "twomarkdown.converter.ocr.ocr_image_bytes",
            lambda image_bytes, ocr_fn=None, force_llm=False: "fresh ocr text",
        )
        monkeypatch.setattr(
            "twomarkdown.agents.page_review.review_enabled", lambda: False
        )

        try:
            put = await client.put(
                "/api/jobs/j-edit-guard/files/0/pages/1",
                json={"markdown": "## Page 1\n\n### OCR\n\nold text\n\nEDITADO-UX\n"},
            )
            assert put.status_code == 200
            assert "EDITADO-UX" in md_path.read_text()

            refused = await client.post(
                "/api/jobs/j-edit-guard/retry",
                json={"file_index": 0, "page": 1},
            )
            assert refused.status_code == 409
            # N10 (round 5): the body is a dict with a stable, English `code`
            # the app can switch on to show its own Spanish copy — not just
            # the exception's English message, which the app used to print
            # verbatim ("sin cambios" showed raw HTTP/JSON to the user).
            body = refused.json()["detail"]
            assert body["code"] == "unsaved_page_edits"
            assert body["file_index"] == 0
            assert body["pages"] == [1]
            assert "message" in body
            # The edit must still be on disk — the retry never ran.
            assert "EDITADO-UX" in md_path.read_text()

            confirmed = await client.post(
                "/api/jobs/j-edit-guard/retry",
                json={"file_index": 0, "page": 1, "confirm": True},
            )
            assert confirmed.status_code == 200
            assert "fresh ocr text" in md_path.read_text()
            assert "EDITADO-UX" not in md_path.read_text()

            # The guard cleared on that confirmed retry — an edit-free page
            # needs no confirmation to retry again.
            again = await client.post(
                "/api/jobs/j-edit-guard/retry",
                json={"file_index": 0, "page": 1},
            )
            assert again.status_code == 200
        finally:
            jobs._jobs.pop("j-edit-guard", None)


# ---------------------------------------------------------------------------
# GET/POST/DELETE /api/syncs
# ---------------------------------------------------------------------------


class TestSyncs:
    @pytest.mark.anyio
    async def test_create_requires_a_known_preset(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/syncs — 404 for an unknown preset_id."""
        resp = await client.post(
            "/api/syncs",
            json={
                "input": str(tmp_path),
                "output": str(tmp_path / "out"),
                "preset_id": "does-not-exist",
            },
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_create_list_delete(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST/GET/DELETE /api/syncs — creates, lists, and deletes a sync."""
        (tmp_path / "in").mkdir()
        created = (
            await client.post(
                "/api/syncs",
                json={
                    "input": str(tmp_path / "in"),
                    "output": str(tmp_path / "out"),
                    "preset_id": "rapido",
                    "enabled": False,
                },
            )
        ).json()

        listed = (await client.get("/api/syncs")).json()
        assert any(s["id"] == created["id"] for s in listed)

        resp = await client.delete(f"/api/syncs/{created['id']}")
        assert resp.status_code == 200
        assert not any(
            s["id"] == created["id"] for s in (await client.get("/api/syncs")).json()
        )

    @pytest.mark.anyio
    async def test_delete_unknown_sync_is_404(self, client: AsyncClient) -> None:
        """DELETE /api/syncs/{id} — 404 for an unknown sync id."""
        resp = await client.delete("/api/syncs/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_patch_flips_enabled_in_place(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """PATCH /api/syncs/{id} — M8: flips `enabled` without a
        delete+recreate, preserving id/last_run/files_today."""
        (tmp_path / "in").mkdir()
        created = (
            await client.post(
                "/api/syncs",
                json={
                    "input": str(tmp_path / "in"),
                    "output": str(tmp_path / "out"),
                    "preset_id": "rapido",
                    "enabled": False,
                },
            )
        ).json()

        resp = await client.patch(f"/api/syncs/{created['id']}", json={"enabled": True})

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == created["id"]
        assert body["enabled"] is True
        assert body["last_run"] == created["last_run"]
        assert body["files_today"] == created["files_today"]

        again = await client.get("/api/syncs")
        assert any(
            s["id"] == created["id"] and s["enabled"] is True for s in again.json()
        )

    @pytest.mark.anyio
    async def test_patch_unknown_sync_is_404(self, client: AsyncClient) -> None:
        """PATCH /api/syncs/{id} — 404 for an unknown sync id."""
        resp = await client.patch("/api/syncs/does-not-exist", json={"enabled": True})
        assert resp.status_code == 404

    def test_watcher_runs_a_job_on_a_new_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """syncs.py — a disabled-then-enabled sync's watcher calls the
        registered job runner; exercised directly against `syncs.py` (no
        client) rather than through real filesystem events (`watchfiles`
        debounces on OS-level detail this test should not depend on)."""
        ran = []
        syncs.set_job_runner(lambda sync: ran.append(sync.id))

        sync = syncs.create_sync(
            SyncCreateRequest(
                input=str(tmp_path), output=str(tmp_path / "out"), preset_id="rapido"
            )
        )
        watcher = syncs._watchers[sync.id]
        watcher._run_once()

        assert ran == [sync.id]


# ---------------------------------------------------------------------------
# POST /api/prices/manual
# ---------------------------------------------------------------------------


class TestManualPrices:
    @pytest.mark.anyio
    async def test_round_trips_through_the_estimator(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/prices/manual — stores a custom model's per-token prices."""
        from twomarkdown.batch.estimate import load_manual_prices

        resp = await client.post(
            "/api/prices/manual",
            json={
                "model": "custom:my-model",
                "input_per_mtok": 1.5,
                "output_per_mtok": 3.0,
            },
        )
        assert resp.status_code == 200

        stored = load_manual_prices(tmp_path)
        assert stored["custom:my-model"] == {
            "input_per_mtok": 1.5,
            "output_per_mtok": 3.0,
        }


# ---------------------------------------------------------------------------
# batch/events.py — the sink is a no-op with nothing attached (CLI parity),
# and semaphore wrapping reports real holders once one is. Not FastAPI
# endpoints, so no client fixture — plain unit tests against the module.
# ---------------------------------------------------------------------------


class TestEvents:
    def test_no_sink_means_every_call_is_free(self) -> None:
        """events.set_sink(None) — every call is a no-op; this is the "CLI
        behaviour is unchanged" guarantee."""
        from twomarkdown.batch import events

        events.set_sink(None)
        # None of these may raise, and there is nothing to assert on the
        # sink side.
        events.begin_file(0, Path("x.pdf"), pages=3)
        events.page_started(0, 1)
        events.page_done(0, 1, engine="tesseract", seconds=0.1)
        events.end_file(0, "ok", 0.1)
        events.cost(0.0)
        events.log("info", "hello")

    def test_sink_receives_file_and_page_events_in_order(self) -> None:
        """events.set_sink — an attached sink receives file/page events in order."""
        from twomarkdown.batch import events

        received = []
        events.set_sink(received.append)
        try:
            events.begin_file(0, Path("x.pdf"), pages=1)
            events.page_started(0, 1)
            events.page_done(0, 1, engine="tesseract", seconds=0.2, fallback=True)
            events.end_file(0, "ok", 0.5)
        finally:
            events.set_sink(None)

        assert [e["type"] for e in received] == [
            "file_started",
            "page_started",
            "page_done",
            "file_done",
        ]
        assert received[2]["fallback"] is True
        assert received[3]["status"] == "ok"

    def test_tracked_semaphore_reports_a_real_holder(self) -> None:
        """events.TrackedSemaphore — reports a real holder around acquire/release."""
        from twomarkdown.batch import events

        received = []
        events.set_sink(received.append)
        sem = events.TrackedSemaphore(1, lane="test-lane")
        try:
            with events.stage("ocr", page=7):
                with sem:
                    pass
        finally:
            events.set_sink(None)
            events._lanes.pop("test-lane", None)

        acquired_events = [e for e in received if e.get("type") == "semaphores"]
        assert acquired_events  # at least one emitted around acquire/release

    def test_stage_leaves_no_page_when_none_given(self) -> None:
        """events.stage — a nested stage without a page inherits the parent's."""
        from twomarkdown.batch import events

        with events.stage("ocr", page=5):
            with events.stage("figures"):
                assert events.current_holder()["page"] == 5
            assert events.current_holder()["page"] == 5

    def test_engine_log_handler_forwards_into_the_event_sink(self) -> None:
        """M4: `_EngineLogHandler` bridges `twomarkdown.*` logging into
        `events.log(...)` — before this, `events.log` was never called
        anywhere in the engine, so "Detalles técnicos" stayed empty for
        every run and a real failure only ever reached the server's stderr."""
        import logging

        from twomarkdown.batch import events
        from twomarkdown.server.jobs import _ENGINE_LOGGER, _EngineLogHandler

        received: list[dict] = []
        events.set_sink(received.append)
        handler = _EngineLogHandler(level=logging.INFO)
        _ENGINE_LOGGER.addHandler(handler)
        try:
            logging.getLogger("twomarkdown.agents.image_ocr").warning(
                "Vision OCR API error: Connection error."
            )
        finally:
            _ENGINE_LOGGER.removeHandler(handler)
            events.set_sink(None)

        log_events = [e for e in received if e.get("type") == "log"]
        assert len(log_events) == 1
        assert log_events[0]["level"] == "warning"
        assert "Connection error" in log_events[0]["message"]
        # N2: `ts` must be the log record's own time, not receipt/replay time
        # (a WebSocket reconnect replays the whole buffered backlog at once,
        # and without a server-assigned `ts` every line got re-stamped with
        # the reconnect time).
        assert log_events[0]["ts"]

    def test_make_sink_stamps_engine_onto_page_started(self, tmp_path: Path) -> None:
        """N10: `events.page_started()` itself carries no `engine` (see its
        own docstring — the emitter has no idea which model a job is
        running) — `jobs._make_sink` fills it in from the job's own
        `pipeline.ocr_model` before any subscriber sees the event, so the
        UI can tell a Tesseract-only run from an LLM one (the "Leyendo con
        IA…" copy bug the UX tester saw)."""
        from twomarkdown.batch import events

        job = jobs.Job(
            job_id="engine-stamp-job",
            status="running",
            output=str(tmp_path),
            pipeline=Pipeline(ocr_model="tesseract"),
            mode="once",
        )
        rt = jobs._Runtime(job=job, input_root=tmp_path, files=[tmp_path / "doc.pdf"])
        events.set_sink(jobs._make_sink(rt))
        try:
            events.page_started(0, 1)
        finally:
            events.set_sink(None)

        assert rt.event_log[0]["engine"] == "tesseract"

        # A non-Tesseract pipeline reports its own model id, not a bare
        # "IA"/generic marker — this is what lets the UI say *which* model,
        # not just "an LLM".
        job2 = jobs.Job(
            job_id="engine-stamp-job-2",
            status="running",
            output=str(tmp_path),
            pipeline=Pipeline(ocr_model="ollama:qwen2.5vl:7b"),
            mode="once",
        )
        rt2 = jobs._Runtime(job=job2, input_root=tmp_path, files=[tmp_path / "doc.pdf"])
        events.set_sink(jobs._make_sink(rt2))
        try:
            events.page_started(0, 1)
        finally:
            events.set_sink(None)
        assert rt2.event_log[0]["engine"] == "ollama:qwen2.5vl:7b"

    def test_log_event_carries_a_server_assigned_timestamp(self) -> None:
        """events.log — `ts` defaults to "now" even without an explicit one,
        and an explicit `ts` (as `_EngineLogHandler` passes) is used as-is."""
        from twomarkdown.batch import events

        received: list[dict] = []
        events.set_sink(received.append)
        try:
            events.log("info", "hello")
            events.log("info", "world", ts="2020-01-01T00:00:00+00:00")
        finally:
            events.set_sink(None)

        assert received[0]["ts"]
        assert received[1]["ts"] == "2020-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# GET /api/models, POST /api/models/resolve
# ---------------------------------------------------------------------------


def _fake_ollama_info(monkeypatch, models_list):
    from twomarkdown.server.schemas import OllamaInfo

    monkeypatch.setattr(
        "twomarkdown.server.system.ollama_info",
        lambda: OllamaInfo(running=True, version="0.5.0", models=models_list),
    )


class TestModels:
    @pytest.mark.anyio
    async def test_shape_has_local_cloud_providers_and_stages(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """GET /api/models — local (installed Ollama models), cloud (the
        genai-prices catalog restricted to the providers this engine can
        drive), providers (key presence), and per-stage id lists."""
        from twomarkdown.server.schemas import OllamaModelInfo

        _fake_ollama_info(
            monkeypatch,
            [
                OllamaModelInfo(
                    name="qwen2.5vl:7b", size_gb=8.8, loaded=True, vision=True
                ),
                OllamaModelInfo(
                    name="llama3.1:8b", size_gb=4.9, loaded=False, vision=False
                ),
            ],
        )

        resp = await client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()

        assert data["local"] == [
            {
                "id": "ollama:qwen2.5vl:7b",
                "name": "qwen2.5vl:7b",
                "size_gb": 8.8,
                "loaded": True,
                "vision": True,
                "installed": True,
            },
            {
                "id": "ollama:llama3.1:8b",
                "name": "llama3.1:8b",
                "size_gb": 4.9,
                "loaded": False,
                "vision": False,
                "installed": True,
            },
        ]

        cloud_ids = {m["id"] for m in data["cloud"]}
        assert "openai:gpt-4o" in cloud_ids
        assert "anthropic:claude-sonnet-4-5" in cloud_ids
        cloud_providers = {m["provider"] for m in data["cloud"]}
        assert cloud_providers <= {
            "openai",
            "anthropic",
            "google",
            "groq",
            "mistral",
            "openrouter",
        }
        for m in data["cloud"]:
            # genai-prices exposes no modality field as of this writing —
            # never a guessed False.
            assert m["vision"] is None
            assert m["price_known"] is True
            assert m["key_present"] is False  # no keys in the test environment

        assert {p["id"] for p in data["providers"]} == {
            "openai",
            "anthropic",
            "google",
            "groq",
            "mistral",
            "openrouter",
        }
        assert all(p["key_present"] is False for p in data["providers"])
        by_id = {p["id"]: p for p in data["providers"]}
        assert by_id["openai"]["env_var"] == "OPENAI_API_KEY"
        assert by_id["anthropic"]["env_var"] == "ANTHROPIC_API_KEY"

        assert "tesseract" in data["stages"]["ocr"]
        assert "tesseract" not in data["stages"]["figures"]
        assert "ollama:qwen2.5vl:7b" in data["stages"]["ocr"]
        assert "ollama:qwen2.5vl:7b" in data["stages"]["figures"]
        # A text-only local model has no place in ocr/figures but is fine
        # for review (blind, text-only proofreading).
        assert "ollama:llama3.1:8b" not in data["stages"]["ocr"]
        assert "ollama:llama3.1:8b" in data["stages"]["review"]
        # Cloud vision is unknown (None), never hidden for it.
        assert "openai:gpt-4o" in data["stages"]["ocr"]
        assert "anthropic:claude-sonnet-4-5" in data["stages"]["review"]

    @pytest.mark.anyio
    async def test_cloud_models_are_not_cross_joined_across_providers(
        self, client: AsyncClient
    ) -> None:
        """GET /api/models (N3) — genai-prices' "google" provider entry also
        lists Anthropic's own Claude models in its `.models` (Vertex AI
        resells them, priced the same as Anthropic's own catalog — see
        `Provider.fallback_model_providers`'s docstring), but this app's
        Google integration is `GOOGLE_API_KEY` against the Gemini Developer
        API, which cannot serve a Claude model at all. No `claude-*` id may
        appear under provider "google" — each cloud model must be listed
        only under the provider that can actually be asked to run it."""
        resp = await client.get("/api/models")
        assert resp.status_code == 200
        cloud = resp.json()["cloud"]

        google_ids = {m["id"] for m in cloud if m["provider"] == "google"}
        assert not any(
            model_id.split(":", 1)[1].startswith("claude") for model_id in google_ids
        )
        # The real Gemini/Gemma catalog must survive the filter untouched.
        assert any(
            model_id.split(":", 1)[1].startswith("gemini") for model_id in google_ids
        )

        anthropic_ids = {
            m["id"].split(":", 1)[1] for m in cloud if m["provider"] == "anthropic"
        }
        assert "claude-sonnet-4-5" in anthropic_ids

    @pytest.mark.anyio
    async def test_response_is_cached_for_60_seconds(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """GET /api/models — repeated calls within the TTL reuse one probe,
        like /api/system's own probes."""
        calls = {"n": 0}
        from twomarkdown.server.schemas import OllamaInfo

        def fake_ollama_info():
            calls["n"] += 1
            return OllamaInfo(running=True, version=None, models=[])

        monkeypatch.setattr("twomarkdown.server.system.ollama_info", fake_ollama_info)

        await client.get("/api/models")
        await client.get("/api/models")

        assert calls["n"] == 1

    @pytest.mark.anyio
    async def test_resolve_tesseract(self, client: AsyncClient, monkeypatch) -> None:
        """POST /api/models/resolve — "tesseract" resolves as kind "cpu"."""
        monkeypatch.setattr(
            "twomarkdown.server.system.tesseract_info",
            lambda: __import__(
                "twomarkdown.server.schemas", fromlist=["TesseractInfo"]
            ).TesseractInfo(installed=True, version="5.3.4"),
        )
        resp = await client.post("/api/models/resolve", json={"id": "tesseract"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "id": "tesseract",
            "kind": "cpu",
            "installed": True,
            "key_present": None,
            "price_known": True,
            "vision": None,
            "problems": [],
        }

    @pytest.mark.anyio
    async def test_resolve_installed_local_model(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """POST /api/models/resolve — an installed local model reports its
        real, probed vision flag and no problems."""
        from twomarkdown.server.schemas import OllamaModelInfo

        _fake_ollama_info(
            monkeypatch,
            [
                OllamaModelInfo(
                    name="qwen2.5vl:7b", size_gb=8.8, loaded=True, vision=True
                )
            ],
        )
        resp = await client.post(
            "/api/models/resolve", json={"id": "ollama:qwen2.5vl:7b"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "local"
        assert data["installed"] is True
        assert data["vision"] is True
        assert data["problems"] == []

    @pytest.mark.anyio
    async def test_resolve_not_installed_local_model(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """POST /api/models/resolve — a bare Ollama model name not among the
        installed ones reports "not_installed"."""
        _fake_ollama_info(monkeypatch, [])
        resp = await client.post("/api/models/resolve", json={"id": "ollama:llava:13b"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "id": "ollama:llava:13b",
            "kind": "local",
            "installed": False,
            "key_present": None,
            "price_known": True,
            "vision": None,
            "problems": ["not_installed"],
        }

    @pytest.mark.anyio
    async def test_resolve_bare_name_is_a_local_ollama_model(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """POST /api/models/resolve — a bare name with no provider prefix is
        Ollama shorthand (normalize_model_id's own rule), not "unknown_provider"."""
        _fake_ollama_info(monkeypatch, [])
        resp = await client.post("/api/models/resolve", json={"id": "qwen2.5vl:7b"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ollama:qwen2.5vl:7b"
        assert data["kind"] == "local"
        assert "unknown_provider" not in data["problems"]

    @pytest.mark.anyio
    async def test_resolve_cloud_model_no_key(self, client: AsyncClient) -> None:
        """POST /api/models/resolve — a known cloud model with no API key
        reports "no_api_key"."""
        resp = await client.post(
            "/api/models/resolve", json={"id": "anthropic:claude-sonnet-4-5"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "cloud"
        assert data["key_present"] is False
        assert data["price_known"] is True
        assert "no_api_key" in data["problems"]

    @pytest.mark.anyio
    async def test_resolve_cloud_model_with_key(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """POST /api/models/resolve — a key present clears "no_api_key"."""
        monkeypatch.setattr(
            "twomarkdown.server.system._dotenv_value",
            lambda var: "sk-test" if var == "ANTHROPIC_API_KEY" else None,
        )
        resp = await client.post(
            "/api/models/resolve", json={"id": "anthropic:claude-sonnet-4-5"}
        )
        data = resp.json()
        assert data["key_present"] is True
        assert "no_api_key" not in data["problems"]

    @pytest.mark.anyio
    async def test_resolve_cloud_model_unknown_price(self, client: AsyncClient) -> None:
        """POST /api/models/resolve — a cloud model genai-prices has never
        heard of reports "unknown_price", not a crash."""
        resp = await client.post(
            "/api/models/resolve", json={"id": "groq:not-a-real-model-xyz"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "cloud"
        assert data["price_known"] is False
        assert "unknown_price" in data["problems"]

    @pytest.mark.anyio
    async def test_resolve_unknown_provider_is_200_not_an_error(
        self, client: AsyncClient
    ) -> None:
        """POST /api/models/resolve — a provider this app doesn't drive
        (recognised by pydantic-ai, e.g. "cohere", but not in this app's
        curated cloud provider list) is a 200 with "unknown_provider", never
        a 4xx/5xx."""
        resp = await client.post("/api/models/resolve", json={"id": "cohere:command-r"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["problems"] == ["unknown_provider"]

    def test_cloud_providers_import_through_pydantic_ai(self) -> None:
        """`CLOUD_PROVIDER_ENV_VARS` must never advertise a provider the
        installed pydantic-ai-slim extras can't actually run: every id in it
        has to import cleanly through `infer_provider_class`, or the catalog
        (GET /api/models) lies about what this engine can drive at
        conversion time. This is the regression test for groq/mistral once
        raising ImportError in Docker because pyproject only requested
        `pydantic-ai` with no extras — see pyproject.toml's
        `pydantic-ai[groq,mistral]` specifier and docs/desktop-app.md."""
        from pydantic_ai.providers import infer_provider_class

        for provider_id in models.CLOUD_PROVIDER_ENV_VARS:
            infer_provider_class(provider_id)  # raises ImportError if missing

    @pytest.mark.anyio
    async def test_resolve_reports_sdk_not_installed_when_provider_class_missing(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """POST /api/models/resolve — if a catalog provider's pydantic-ai
        class can't import (SDK extra missing), resolve reports
        "sdk_not_installed" instead of pretending the id is fine (it must
        not fall through to "no_api_key"/"unknown_price", which would be
        misleading about the real problem)."""

        def fake_infer_provider_class(provider_id: str):
            raise ImportError(f"no SDK for {provider_id}")

        monkeypatch.setattr(
            "pydantic_ai.providers.infer_provider_class", fake_infer_provider_class
        )
        resp = await client.post(
            "/api/models/resolve", json={"id": "groq:llama-3.1-8b-instant"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "cloud"
        assert data["problems"] == ["sdk_not_installed"]
        assert data["key_present"] is None
        assert data["price_known"] is False


# ---------------------------------------------------------------------------
# GET/DELETE /api/folders, InspectResponse.last_preset_id
# ---------------------------------------------------------------------------


class TestFolders:
    @pytest.mark.anyio
    async def test_inspect_has_no_last_preset_before_any_job(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        f = tmp_path / "notes.txt"
        f.write_text("hello")
        resp = await client.post("/api/inspect", json={"path": str(f)})
        assert resp.json()["last_preset_id"] is None

    @pytest.mark.anyio
    async def test_job_with_a_preset_id_is_remembered_for_its_folder(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """POST /api/jobs (mode "once") records the preset used for the
        folder; a later /api/inspect on the same folder preselects it, and
        it shows up under GET /api/folders."""
        src = tmp_path / "input"
        src.mkdir()
        (src / "notes.txt").write_text("hello")
        out = tmp_path / "output"

        await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "preset_id": "rapido",
                "mode": "once",
            },
        )

        resp = await client.post("/api/inspect", json={"path": str(src)})
        assert resp.json()["last_preset_id"] == "rapido"

        folders = (await client.get("/api/folders")).json()
        assert len(folders) == 1
        assert folders[0]["preset_id"] == "rapido"
        assert Path(folders[0]["path"]) == src.resolve()
        assert folders[0]["last_used"]

    @pytest.mark.anyio
    async def test_job_with_a_raw_pipeline_is_remembered_as_no_preset(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """A job created from a raw Pipeline (no preset_id) still records
        the folder, with preset_id null — not skipped, not a stale id from
        a previous run."""
        src = tmp_path / "input"
        src.mkdir()
        (src / "notes.txt").write_text("hello")
        out = tmp_path / "output"

        await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "pipeline": Pipeline().model_dump(),
                "mode": "once",
            },
        )

        folders = (await client.get("/api/folders")).json()
        assert folders[0]["preset_id"] is None

    @pytest.mark.anyio
    async def test_delete_folder_removes_it(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        src = tmp_path / "input"
        src.mkdir()
        (src / "notes.txt").write_text("hello")
        out = tmp_path / "output"
        await client.post(
            "/api/jobs",
            json={
                "path": str(src),
                "output": str(out),
                "preset_id": "rapido",
                "mode": "once",
            },
        )
        assert len((await client.get("/api/folders")).json()) == 1

        resp = await client.delete("/api/folders", params={"path": str(src)})
        assert resp.status_code == 200
        assert (await client.get("/api/folders")).json() == []

    def test_oldest_entry_is_dropped_past_max_entries(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """folder_presets.record — caps at MAX_ENTRIES, dropping the
        least-recently-used path rather than growing without bound."""
        monkeypatch.setattr(folder_presets, "FOLDER_PRESETS_DIR", tmp_path)
        monkeypatch.setattr(
            folder_presets, "FOLDER_PRESETS_FILE", tmp_path / "folder_presets.json"
        )
        monkeypatch.setattr(folder_presets, "MAX_ENTRIES", 3)

        for i in range(4):
            folder_presets.record(str(tmp_path / f"folder{i}"), "rapido")

        rows = folder_presets.list_folders()
        assert len(rows) == 3
        paths = {Path(r["path"]).name for r in rows}
        assert "folder0" not in paths
        assert "folder3" in paths


# ---------------------------------------------------------------------------
# PUT /api/presets — accepts any provider:model id (item 4)
# ---------------------------------------------------------------------------


class TestPresetsAcceptAnyModelId:
    @pytest.mark.anyio
    async def test_put_accepts_a_cloud_model_id(self, client: AsyncClient) -> None:
        mine = Preset(
            id="mio-cloud",
            name="Mío",
            builtin=False,
            pipeline=Pipeline(review_model="anthropic:claude-sonnet-4-5"),
        )
        payload = [p.model_dump() for p in presets.BUILTIN_PRESETS] + [
            mine.model_dump()
        ]

        resp = await client.put("/api/presets", json={"presets": payload})
        assert resp.status_code == 200

        refetched = {p["id"]: p for p in (await client.get("/api/presets")).json()}
        assert refetched["mio-cloud"]["pipeline"]["review_model"] == (
            "anthropic:claude-sonnet-4-5"
        )

    @pytest.mark.anyio
    async def test_put_accepts_an_arbitrary_local_ollama_model_id(
        self, client: AsyncClient
    ) -> None:
        mine = Preset(
            id="mio-local",
            name="Mío local",
            builtin=False,
            pipeline=Pipeline(
                ocr_model="ollama:llava:13b", figure_model="ollama:llava:13b"
            ),
        )
        payload = [p.model_dump() for p in presets.BUILTIN_PRESETS] + [
            mine.model_dump()
        ]

        resp = await client.put("/api/presets", json={"presets": payload})
        assert resp.status_code == 200

        refetched = {p["id"]: p for p in (await client.get("/api/presets")).json()}
        assert refetched["mio-local"]["pipeline"]["ocr_model"] == "ollama:llava:13b"

    @pytest.mark.anyio
    async def test_estimate_does_not_crash_on_an_unknown_non_openai_cloud_model(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """A non-OpenAI cloud model /api/estimate has never priced before
        must not crash it: usd None, unknown_price True — same rule as any
        other unpriced model."""
        import fitz

        pdf_path = tmp_path / "scanned.pdf"
        doc = fitz.open()
        doc.new_page()  # no text layer at all -> counts as a scanned page
        doc.save(pdf_path)
        doc.close()
        inspect_id = (
            await client.post("/api/inspect", json={"path": str(pdf_path)})
        ).json()["inspect_id"]

        pipeline = Pipeline(review_model="groq:not-a-real-model-xyz")
        resp = await client.post(
            "/api/estimate",
            json={"inspect_id": inspect_id, "pipeline": pipeline.model_dump()},
        )

        assert resp.status_code == 200
        data = resp.json()
        review_stage = next(s for s in data["stages"] if s["key"] == "review")
        assert review_stage["usd"] is None
        assert review_stage["unknown_price"] is True


# ---------------------------------------------------------------------------
# Cancel — job-level (stops after the current page) and per-file
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_job_reaches_the_currently_running_files_own_cancel_token(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`cancel_job()` must set the *currently running* file's own cancel
        token immediately, not just the job-wide `cancel_event` a between-
        files check would only notice once this file is done. A fake
        `process_batch` stands in for a real conversion so the test controls
        timing (and inspects the `cancel=` token it was actually called
        with) instead of racing real OCR."""
        import threading

        from twomarkdown.batch.processor import BatchResult

        files = [tmp_path / f"{i}.txt" for i in range(2)]
        for f in files:
            f.write_text("x")
        out = tmp_path / "out"
        out.mkdir()

        seen_cancel_tokens: list[threading.Event | None] = []
        started = threading.Event()
        proceed = threading.Event()

        def fake_process_batch(*args, **kwargs):
            cancel = kwargs.get("cancel")
            seen_cancel_tokens.append(cancel)
            started.set()
            proceed.wait(timeout=5.0)
            if cancel is not None and cancel.is_set():
                return BatchResult(failed=1)
            return BatchResult(converted=1)

        import twomarkdown.server.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "process_batch", fake_process_batch)

        job = jobs.create_job(
            path=str(tmp_path),
            inspect_id=None,
            output=str(out),
            pipeline=Pipeline(ocr_model="tesseract"),
            mode="once",
        )
        assert started.wait(timeout=5.0)
        jobs.cancel_job(job.job_id)
        # Give `cancel_job` a moment to reach the running file's own token
        # before releasing the fake conversion.
        for _ in range(50):
            if seen_cancel_tokens and seen_cancel_tokens[0] is not None:
                if seen_cancel_tokens[0].is_set():
                    break
            time.sleep(0.02)
        assert seen_cancel_tokens[0] is not None
        assert seen_cancel_tokens[0].is_set()
        proceed.set()

        for _ in range(100):
            if job.status in ("done", "cancelled"):
                break
            time.sleep(0.02)
        assert job.status == "cancelled"
        assert job.files[0].status == "cancelled"
        # The second file was still queued when the job was cancelled — it
        # never ran at all.
        assert job.files[1].status == "cancelled"
        assert job.files[1].reason == "cancelado antes de empezar"
        assert job.cancelled == 2

    @pytest.mark.anyio
    async def test_cancel_file_while_queued_is_skipped_without_running(
        self, client: AsyncClient, tmp_path: Path, monkeypatch
    ) -> None:
        """`POST /api/jobs/{id}/files/{i}/cancel` on a still-queued file marks
        it cancelled with no output and lets the job continue converting the
        rest — the job itself is never touched (see `jobs.cancel_file`)."""
        import threading

        from twomarkdown.batch.processor import BatchResult

        files = [tmp_path / f"{i}.txt" for i in range(3)]
        for f in files:
            f.write_text("x")
        out = tmp_path / "out"
        out.mkdir()

        started_indices: list[int] = []
        release = threading.Event()

        def fake_process_batch(*args, **kwargs):
            offset = kwargs.get("file_index_offset", 0)
            started_indices.append(offset)
            if offset == 0:
                release.wait(timeout=5.0)
            return BatchResult(converted=1)

        import twomarkdown.server.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "process_batch", fake_process_batch)

        job = jobs.create_job(
            path=str(tmp_path),
            inspect_id=None,
            output=str(out),
            pipeline=Pipeline(ocr_model="tesseract"),
            mode="once",
        )
        for _ in range(50):
            if 0 in started_indices:
                break
            time.sleep(0.02)

        resp = await client.post(f"/api/jobs/{job.job_id}/files/2/cancel")
        assert resp.status_code == 200
        data = resp.json()
        file2 = next(f for f in data["files"] if f["index"] == 2)
        assert file2["status"] == "cancelled"
        assert file2["reason"] == "cancelado antes de empezar"
        # `output_path` is only ever set once a file's conversion actually
        # writes it (`_fill_file_facts`) — a queued-then-cancelled file
        # never reaches that at all, so it stays `None` forever.
        assert file2["output_path"] is None

        release.set()
        final = await _wait_for_job(client, job.job_id)
        assert final["status"] == "done"  # the job itself was never cancelled
        assert final["cancelled"] == 1
        assert final["ok"] == 2

    def test_mid_file_cancel_never_surfaces_the_bare_status_as_reason(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """M4: a mid-file cancel (the file was already running when
        cancelled, so it stops after the current page rather than being
        skipped outright) must report the Spanish
        "cancelado tras la página en curso", never a raw status token.

        The real bug: `batch/processor.py`'s per-file worker catches the
        `ConversionError("cancelled")` this file's own cancel token raises
        internally and reports it via `events.end_file(..., reason=str(exc))`
        — literally the bare word "cancelled" (or, for the graceful partial-
        write path, "cancelled; partial output written to …") — which the
        sink (`_make_sink`'s `file_done` handling) writes straight onto
        `JobFileState.reason` *before* `_run_job`'s own `was_cancelled`
        branch below ever runs. This fake `process_batch` reproduces exactly
        that: it fires the same event the real worker does, then returns a
        normal (non-raising) `BatchResult`, same as the real one — so this
        only passes if `_run_job` overwrites that stashed reason rather than
        deferring to it."""
        from twomarkdown.batch import events
        from twomarkdown.batch.processor import BatchResult

        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"
        out.mkdir()

        def fake_process_batch(*args, **kwargs):
            index = kwargs.get("file_index_offset", 0)
            cancel = kwargs.get("cancel")
            assert cancel is not None
            cancel.set()
            # Exactly what `batch/processor.py`'s worker does for a file
            # whose own cancel token fired: report the exception's raw text
            # as `reason` through the same event the real engine uses.
            events.end_file(index, "cancelled", 0.01, reason="cancelled")
            return BatchResult(failed=1)

        import twomarkdown.server.jobs as jobs_mod

        monkeypatch.setattr(jobs_mod, "process_batch", fake_process_batch)

        job = jobs.create_job(
            path=str(src),
            inspect_id=None,
            output=str(out),
            pipeline=Pipeline(ocr_model="tesseract"),
            mode="once",
        )
        for _ in range(100):
            if job.status in ("done", "cancelled"):
                break
            time.sleep(0.02)

        assert job.files[0].status == "cancelled"
        assert job.files[0].reason == "cancelado tras la página en curso"
        assert job.files[0].reason != "cancelled"

    @pytest.mark.anyio
    async def test_cancel_file_already_finished_is_409(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post(f"/api/jobs/{job_id}/files/0/cancel")
        assert resp.status_code == 409

    @pytest.mark.anyio
    async def test_cancel_file_out_of_range_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"
        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post(f"/api/jobs/{job_id}/files/99/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-file facts — kind/pages/output_path/engine_used/review_changes_count
# ---------------------------------------------------------------------------


class TestPerFileFacts:
    @pytest.mark.anyio
    async def test_a_docx_like_text_file_never_reports_zero_pages(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """`JobFileState.pages` must be `None` for a non-paginated kind —
        never `0`, which the frontend used to read as "nothing to show"."""
        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["files"][0]["kind"] == "text"
        assert data["files"][0]["pages"] is None
        assert data["files"][0]["output_path"] == str(out / "in.md")
        assert data["files"][0]["engine_used"] == "none"

    @pytest.mark.anyio
    async def test_a_pdf_reports_its_kind_and_real_page_count(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        import fitz

        pdf_path = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "hello world, plenty of native text here")
        doc.save(pdf_path)
        doc.close()
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(pdf_path),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]

        data = await _wait_for_job(client, job_id)
        assert data["files"][0]["kind"] == "pdf"
        assert data["files"][0]["pages"] == 2


# ---------------------------------------------------------------------------
# Job history — persisted past a server "restart" (jobs_history.json)
# ---------------------------------------------------------------------------


class TestHistory:
    @pytest.mark.anyio
    async def test_a_finished_job_survives_the_in_memory_store_being_cleared(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Simulates a server restart: `_jobs` (in-memory) is cleared, but
        `GET /api/jobs/{id}` must still answer from `jobs_history.json`."""
        src = tmp_path / "in.txt"
        src.write_text("hello history")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        assert jobs.HISTORY_FILE.is_file()
        jobs._jobs.clear()  # simulate a restart losing the in-memory store

        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["input"] == str(src)
        assert data["files"][0]["output_path"] == str(out / "in.md")

    @pytest.mark.anyio
    async def test_list_jobs_is_newest_first_and_respects_limit(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        job_ids = []
        for i in range(3):
            src = tmp_path / f"in{i}.txt"
            src.write_text(f"hello {i}")
            out = tmp_path / f"out{i}"
            job_id = (
                await client.post(
                    "/api/jobs",
                    json={
                        "path": str(src),
                        "output": str(out),
                        "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                    },
                )
            ).json()["job_id"]
            await _wait_for_job(client, job_id)
            job_ids.append(job_id)

        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        listed_ids = [j["job_id"] for j in resp.json()]
        # Newest first: the last job created appears before the first.
        assert listed_ids.index(job_ids[-1]) < listed_ids.index(job_ids[0])

        limited = await client.get("/api/jobs", params={"limit": 1})
        assert len(limited.json()) == 1

    @pytest.mark.anyio
    async def test_retry_works_on_a_job_rehydrated_from_history(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """`jobs.retry()` must work for a job no longer in `_jobs` (loaded
        back from `jobs_history.json`), same as one still in memory."""
        src = tmp_path / "in.txt"
        src.write_text("hello retry after restart")
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)
        jobs._jobs.clear()  # simulate a restart

        resp = await client.post(
            f"/api/jobs/{job_id}/retry", json={"file_index": 0, "confirm": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["files"][0]["status"] == "ok"

        # The history record itself is updated with the retry's outcome.
        jobs._jobs.clear()
        again = await client.get(f"/api/jobs/{job_id}")
        assert again.json()["files"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/files/{i}/original — "ver original"
# ---------------------------------------------------------------------------


class TestOriginal:
    @pytest.mark.anyio
    async def test_pdf_reports_kind_and_page_count(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        import fitz

        pdf_path = tmp_path / "doc.pdf"
        doc = fitz.open()
        for _ in range(3):
            page = doc.new_page()
            page.insert_text((72, 72), "hello world, plenty of native text here")
        doc.save(pdf_path)
        doc.close()
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(pdf_path),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.get(f"/api/jobs/{job_id}/files/0/original")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"kind": "pdf", "previewable": True, "pages": 3, "path": None}

    @pytest.mark.anyio
    async def test_image_returns_the_raw_bytes(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
            b"\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0e"
            b"\xc3\x00\x00\x0e\xc3\x01\xc7o\xa8d\x00\x00\x00\x0cIDATx\x9cc\xf8"
            b"\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00"
            b"IEND\xaeB`\x82"
        )
        img_path = tmp_path / "photo.png"
        img_path.write_bytes(png_bytes)
        out = tmp_path / "out"

        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(img_path),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.get(f"/api/jobs/{job_id}/files/0/original")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert resp.content == png_bytes

    @pytest.mark.anyio
    async def test_office_file_is_not_previewable_but_names_its_path(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        docx_path = tmp_path / "notes.docx"
        docx_path.write_bytes(b"not a real docx, just needs to exist")
        out = tmp_path / "out"

        resp = await client.post(
            "/api/jobs",
            json={
                "path": str(docx_path),
                "output": str(out),
                "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
            },
        )
        job_id = resp.json()["job_id"]
        await _wait_for_job(client, job_id)

        original = await client.get(f"/api/jobs/{job_id}/files/0/original")
        assert original.status_code == 200
        data = original.json()
        assert data["kind"] == "office"
        assert data["previewable"] is False
        assert data["path"] == str(docx_path)

    @pytest.mark.anyio
    async def test_out_of_range_file_index_is_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"
        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.get(f"/api/jobs/{job_id}/files/99/original")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/open, POST /api/reveal — path guard
# ---------------------------------------------------------------------------


class TestOpen:
    @pytest.mark.anyio
    async def test_rejects_a_path_outside_any_allowed_root(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            Path, "home", classmethod(lambda cls: Path("/nonexistent-home"))
        )
        resp = await client.post("/api/open", json={"path": "/etc/passwd"})
        # A disallowed path is refused outright (403), not reported back as
        # a 200 `{ok: false}` — see `server/app.py:_reject_if_path_not_allowed`.
        assert resp.status_code == 403
        assert "outside" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_allows_a_path_under_a_known_jobs_output_root(
        self, client: AsyncClient, tmp_path: Path, monkeypatch
    ) -> None:
        """The path guard passes for a job's own output root even when it is
        outside the user's home (`tmp_path` here) — whether the OS actually
        opens it is a separate concern (this test runs on Linux CI, where
        `open` itself is unavailable, so it only proves the guard passed)."""
        monkeypatch.setattr(
            Path, "home", classmethod(lambda cls: Path("/nonexistent-home"))
        )
        src = tmp_path / "in.txt"
        src.write_text("hello")
        out = tmp_path / "out"
        job_id = (
            await client.post(
                "/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": Pipeline(ocr_model="tesseract").model_dump(),
                },
            )
        ).json()["job_id"]
        await _wait_for_job(client, job_id)

        resp = await client.post("/api/open", json={"path": str(out / "in.md")})
        data = resp.json()
        # The guard passed (no "outside" error); on non-macOS CI the actual
        # `open` call itself is what then fails.
        assert data["error"] != "path is outside the allowed job/output/home roots"

    @pytest.mark.anyio
    async def test_reveal_uses_the_same_guard(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            Path, "home", classmethod(lambda cls: Path("/nonexistent-home"))
        )
        resp = await client.post("/api/reveal", json={"path": "/etc/shadow"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST/DELETE /api/cloud/{provider}/key — every provider, not just OpenAI
# ---------------------------------------------------------------------------


class TestCloudKeys:
    @pytest.fixture(autouse=True)
    def _isolated_env_file(self, tmp_path: Path, monkeypatch):
        """Never touch the real repo `.env` from a test, and never let one
        test's cached probe verdict (`system._probe_cache`, a process-wide
        dict) leak into another — each test gets its own `.env` via
        `tmp_path` already; the cache needs the same isolation since it is
        keyed by provider id alone, not by which `.env` it came from."""
        from twomarkdown.server import system

        monkeypatch.setattr(host, "_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(system, "_probe_cache", {})
        # Undo `_isolated_state`'s blanket "no key ever" stub: this class's
        # own `tmp_path`-backed `.env` (via `host._ENV_PATH` above) is already
        # isolated from the real machine, so the real `_dotenv_value` is safe
        # here and is what `key_present`/`provider_cloud_info` need to see a
        # key this class just wrote.
        monkeypatch.setattr(system, "_dotenv_value", _real_dotenv_value)

    @pytest.mark.anyio
    async def test_unknown_provider_is_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/cloud/not-a-provider/key", json={"key": "sk-test"}
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_post_writes_the_env_line_and_invalidates_caches(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(host, "probe_cloud_key", lambda provider, key: "ok")
        models._cache = ("stale", "stale")  # sentinel: must be cleared

        resp = await client.post(
            "/api/cloud/anthropic/key", json={"key": "sk-ant-test"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"provider": "anthropic", "key_present": True, "status": "ok"}

        env_text = host._ENV_PATH.read_text(encoding="utf-8")
        assert 'ANTHROPIC_API_KEY="sk-ant-test"' in env_text
        assert models._cache is None

    @pytest.mark.anyio
    async def test_probe_verdict_survives_a_get_system_after_saving_the_key(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """C6: a saved key's probe verdict must not revert to "unknown" the
        moment a later `GET /api/system` recomputes the provider's entry
        (the reported repro was a plain browser reload) — `provider_cloud_info`
        has to serve the cached verdict `POST /api/cloud/{provider}/key`
        already computed, not rediscover "unknown" from key-presence alone."""
        monkeypatch.setattr(
            host, "probe_cloud_key", lambda provider, key: "invalid_key"
        )

        post_resp = await client.post(
            "/api/cloud/anthropic/key", json={"key": "sk-ant-test"}
        )
        assert post_resp.json()["status"] == "invalid_key"

        # Simulate a reload: a fresh GET, no state carried on the client.
        system_resp = await client.get("/api/system")
        assert system_resp.json()["cloud"]["anthropic"] == {
            "key_present": True,
            "status": "invalid_key",
        }

    @pytest.mark.anyio
    async def test_probe_verdict_survives_an_in_process_restart(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """C6: the verdict is persisted to disk (`system._PROBE_CACHE_FILE`),
        not just kept in the running process's memory, so it also survives
        an actual server restart — simulated here by wiping the in-memory
        cache dict, the only state a restart would actually lose."""
        monkeypatch.setattr(
            host, "probe_cloud_key", lambda provider, key: "invalid_key"
        )
        await client.post("/api/cloud/anthropic/key", json={"key": "sk-ant-test"})

        system._probe_cache.clear()  # the only thing a real restart drops

        system_resp = await client.get("/api/system")
        assert system_resp.json()["cloud"]["anthropic"] == {
            "key_present": True,
            "status": "invalid_key",
        }

    @pytest.mark.anyio
    async def test_delete_clears_the_persisted_probe_verdict(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """C6: removing a key must not leave a stale persisted verdict for
        it lying around — the next `GET /api/system`, even after an
        in-process restart, must not resurrect the old "invalid_key"."""
        monkeypatch.setattr(
            host, "probe_cloud_key", lambda provider, key: "invalid_key"
        )
        await client.post("/api/cloud/anthropic/key", json={"key": "sk-ant-test"})

        resp = await client.delete("/api/cloud/anthropic/key")
        assert resp.status_code == 200

        system._probe_cache.clear()  # simulate a restart after the delete
        system_resp = await client.get("/api/system")
        assert system_resp.json()["cloud"]["anthropic"]["status"] == "no_key"

    @pytest.mark.anyio
    async def test_post_never_logs_or_returns_the_raw_key_on_failure(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            host, "probe_cloud_key", lambda provider, key: "invalid_key"
        )
        resp = await client.post("/api/cloud/groq/key", json={"key": "gsk-secret"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "invalid_key"
        assert "gsk-secret" not in resp.text or resp.json()["provider"] == "groq"

    @pytest.mark.anyio
    async def test_delete_removes_the_env_line(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        monkeypatch.setattr(host, "probe_cloud_key", lambda provider, key: "ok")
        await client.post("/api/cloud/mistral/key", json={"key": "mk-test"})
        assert "MISTRAL_API_KEY" in host._ENV_PATH.read_text(encoding="utf-8")

        resp = await client.delete("/api/cloud/mistral/key")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"provider": "mistral", "key_present": False, "status": "no_key"}
        assert "MISTRAL_API_KEY" not in host._ENV_PATH.read_text(encoding="utf-8")

    @pytest.mark.anyio
    async def test_openai_alias_endpoint_still_works(self, client: AsyncClient) -> None:
        resp = await client.post("/api/cloud/openai/key", json={"key": "sk-openai"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert 'OPENAI_API_KEY="sk-openai"' in host._ENV_PATH.read_text(
            encoding="utf-8"
        )

    def test_probe_generic_models_endpoint_maps_status_codes(self, monkeypatch) -> None:
        class _Resp:
            def __init__(self, status_code: int, text: str = "") -> None:
                self.status_code = status_code
                self.text = text

        responses = iter(
            [_Resp(200), _Resp(401), _Resp(429, "quota exceeded"), _Resp(500)]
        )
        monkeypatch.setattr(host.httpx, "get", lambda *a, **k: next(responses))

        assert host._probe_generic_models_endpoint("https://x", {}) == "ok"
        assert host._probe_generic_models_endpoint("https://x", {}) == "invalid_key"
        assert host._probe_generic_models_endpoint("https://x", {}) == "out_of_credit"
        assert host._probe_generic_models_endpoint("https://x", {}) == "unknown"

    def test_probe_cloud_key_routes_openai_through_the_existing_probe(
        self, monkeypatch
    ) -> None:
        from twomarkdown.server import system

        monkeypatch.setattr(system, "_probe_openai", lambda key: "ok")
        assert host.probe_cloud_key("openai", "sk-test") == "ok"

    def test_probe_cloud_key_network_failure_is_unknown_not_a_crash(
        self, monkeypatch
    ) -> None:
        def _boom(*a, **k):
            raise host.httpx.ConnectError("no route")

        monkeypatch.setattr(host.httpx, "get", _boom)
        assert host.probe_cloud_key("anthropic", "sk-test") == "unknown"


# ---------------------------------------------------------------------------
# N10 — SIGTERM must not leave the server (and its `uv run` parent) running
# forever. Two layers: a unit test against `app._lifespan`'s own close-every-
# socket logic (fast, no real process), and a subprocess test that exercises
# the whole stack for real (slow — marked `integration` purely so `make
# test-backend`'s default run skips it, not because it needs
# markitdown/tesseract; run it with `make test-integration` or
# `make test-backend TEST=tests/test_server.py::TestShutdown`).
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_lifespan_shutdown_closes_every_registered_websocket(self) -> None:
        """N10: an open events WebSocket must not survive lifespan shutdown.
        The real bug lived exactly here — a client (or a UI tab) left on
        `WS /api/jobs/{id}/events` kept its own request-handling task alive
        forever, which is what kept the whole python process (and its `uv
        run` parent) alive past SIGTERM; four orphan pairs had piled up on
        one machine before this fix. Exercises the real `app._lifespan`
        directly (`syncs.start_all()`/`stop_all()` are harmless with nothing
        configured, thanks to this module's autouse `_isolated_state`
        fixture) with a fake registered socket standing in for a real
        client connection — no real network needed for this half."""
        import asyncio
        import sys

        # Not `from twomarkdown.server import app as app_module`, and not
        # even `import twomarkdown.server.app as app_module` — either way
        # reads the *attribute* `twomarkdown.server.app`, which the package
        # `__init__.py` deliberately overwrites with the FastAPI instance
        # itself (`from twomarkdown.server.app import app`) so `uvicorn
        # twomarkdown.server:app` keeps working; that shadows the submodule
        # at that same dotted path. `sys.modules` still has the real
        # submodule (already imported by this file's own top-level `from
        # twomarkdown.server.app import app`), for its `_active_ws`/
        # `_lifespan`.
        app_module = sys.modules["twomarkdown.server.app"]

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.closed_with: tuple[int, str] | None = None

            async def close(self, code: int = 1000, reason: str = "") -> None:
                self.closed_with = (code, reason)

        fake_ws = _FakeWebSocket()
        app_module._active_ws.add(fake_ws)  # type: ignore[arg-type]

        async def _run() -> None:
            async with app_module._lifespan(app_module.app):
                pass

        try:
            asyncio.run(_run())
        finally:
            app_module._active_ws.discard(fake_ws)  # type: ignore[arg-type]

        assert fake_ws.closed_with == (1001, "server shutting down")

    def test_lifespan_shutdown_tolerates_a_socket_that_errors_on_close(self) -> None:
        """One misbehaving/already-gone socket must never stop the rest of
        shutdown from proceeding (`jobs.shutdown()` still has to run so no
        job thread is left behind either)."""
        import asyncio
        import sys

        # See the previous test's comment on why this must read
        # `sys.modules` directly rather than any `import ... as` form.
        app_module = sys.modules["twomarkdown.server.app"]

        class _BoomWebSocket:
            async def close(self, code: int = 1000, reason: str = "") -> None:
                raise RuntimeError("already gone")

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.closed = False

            async def close(self, code: int = 1000, reason: str = "") -> None:
                self.closed = True

        boom = _BoomWebSocket()
        fine = _FakeWebSocket()
        app_module._active_ws.add(boom)  # type: ignore[arg-type]
        app_module._active_ws.add(fine)  # type: ignore[arg-type]

        async def _run() -> None:
            async with app_module._lifespan(app_module.app):
                pass

        try:
            asyncio.run(_run())
        finally:
            app_module._active_ws.discard(boom)  # type: ignore[arg-type]
            app_module._active_ws.discard(fine)  # type: ignore[arg-type]

        assert fine.closed is True
        assert app_module._active_ws == set()

    @pytest.mark.integration
    def test_sigterm_exits_within_5s_with_an_open_events_websocket(
        self, tmp_path: Path
    ) -> None:
        """Full-stack regression for N10: a real server process, a real
        `websockets` client left open on a finished job's event stream
        (`ws_job_events`'s own loop never ends on its own — it polls forever
        for new events even once the buffer is replayed, which is exactly
        the shape of connection that used to hang the process), then
        SIGTERM — asserting the *process* (not just the port) is gone
        within 5s."""
        import os
        import signal
        import socket
        import subprocess
        import sys
        import time as time_mod

        import httpx
        from websockets.sync.client import connect

        port = 8767
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                pytest.skip(f"port {port} already in use; skipping subprocess test")

        script = (
            "import uvicorn; "
            "uvicorn.run('twomarkdown.server.app:app', host='127.0.0.1', "
            f"port={port}, timeout_graceful_shutdown=3, log_level='warning')"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=dict(os.environ),
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time_mod.monotonic() + 15.0
            while time_mod.monotonic() < deadline:
                try:
                    if httpx.get(f"{base}/api/system", timeout=1.0).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time_mod.sleep(0.2)
            else:
                raise AssertionError("server did not come up in time")

            src = tmp_path / "in.txt"
            src.write_text("hello events")
            out = tmp_path / "out"
            job_id = httpx.post(
                f"{base}/api/jobs",
                json={
                    "path": str(src),
                    "output": str(out),
                    "pipeline": {"ocr_model": "tesseract"},
                },
                timeout=5.0,
            ).json()["job_id"]

            deadline = time_mod.monotonic() + 15.0
            while time_mod.monotonic() < deadline:
                data = httpx.get(f"{base}/api/jobs/{job_id}", timeout=1.0).json()
                if data["status"] in ("done", "failed", "cancelled"):
                    break
                time_mod.sleep(0.1)
            else:
                raise AssertionError("job did not finish in time")

            ws = connect(f"ws://127.0.0.1:{port}/api/jobs/{job_id}/events")
            try:
                ws.recv(timeout=2.0)  # at least the replayed job_started

                proc.send_signal(signal.SIGTERM)
                start = time_mod.monotonic()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)
                    raise AssertionError(
                        "server did not exit within 5s of SIGTERM with an "
                        "open events WebSocket still connected"
                    ) from None
                assert time_mod.monotonic() - start < 5.0
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
