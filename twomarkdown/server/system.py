"""What machine and what providers `GET /api/system` reports on.

Every probe here is best-effort and never raises: a local desktop app's system
panel should degrade to "not running" / "unknown" rather than 500 when Ollama
is down or Tesseract isn't installed, and the OpenAI probe in particular must
never retry — an out-of-credit account (see AGENTS.md) answers
`insufficient_quota` on the first try just as reliably as the fifth, and a
retry loop there only burns wall clock confirming the same answer.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

import httpx

from twomarkdown.config import ollama_host
from twomarkdown.server.schemas import (
    CloudInfo,
    OllamaInfo,
    OllamaModelInfo,
    OpenAIInfo,
    OpenAIStatus,
    PricesInfo,
    ProviderCloudInfo,
    SystemInfo,
    TesseractInfo,
)

# Env var each non-OpenAI cloud provider in `/api/system`'s `cloud` and
# `server/models.py`'s catalog reads its key from — pydantic-ai's own default
# (see each Provider class's docstring), not a name this app invented.
_PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

logger = logging.getLogger(__name__)

# Same host `config.ollama_host()` resolves for `llm_config.ollama_base_url`
# — one source of truth for "where is Ollama", so this probe and the actual
# conversion calls can never disagree about whether Ollama is reachable (see
# B1 in the desktop-app review: they used to be two different constants).
_OLLAMA_BASE = f"http://{ollama_host()}:11434"
_PROBE_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Machine
# ---------------------------------------------------------------------------


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value or None


def ram_gb() -> float:
    raw = _sysctl("hw.memsize")
    if raw is None:
        return 0.0
    try:
        return round(int(raw) / 1_000_000_000, 1)
    except ValueError:
        return 0.0


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def gpu_limit_gb(total_ram_gb: float) -> float:
    """Unified memory the GPU can use, on the one platform that has it.

    Apple Silicon shares RAM between CPU and GPU; macOS reserves the rest for
    itself and everything else running. 75% is the same rule of thumb the
    mockup's Pipeline simulator uses (36 GB of 48 GB), not a measured ceiling —
    Metal has no public API to ask for the real one.
    """
    if not is_apple_silicon():
        return 0.0
    return round(total_ram_gb * 0.75, 1)


def cpu_cores() -> int:
    return os.cpu_count() or 1


def chip_name() -> str:
    # machdep.cpu.brand_string reports "Apple M2 Pro" on Apple Silicon too,
    # despite the Intel-era sysctl name; it's the only place macOS exposes it.
    brand = _sysctl("machdep.cpu.brand_string")
    return brand or platform.machine() or platform.processor() or "unknown"


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------


def tesseract_info() -> TesseractInfo:
    import shutil

    path = shutil.which("tesseract")
    if path is None:
        return TesseractInfo(installed=False, version=None)
    version: str | None = None
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        first_line = (out.stdout or out.stderr or "").splitlines()
        if first_line:
            # "tesseract 5.3.4" -> "5.3.4"
            version = first_line[0].split()[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    return TesseractInfo(installed=True, version=version)


# ---------------------------------------------------------------------------
# Ollama — /api/tags (installed models) + /api/ps (what's actually loaded)
# ---------------------------------------------------------------------------


def ollama_info() -> OllamaInfo:
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            tags = client.get(f"{_OLLAMA_BASE}/api/tags")
            tags.raise_for_status()
            tags_data = tags.json() or {}
            try:
                version_resp = client.get(f"{_OLLAMA_BASE}/api/version")
                version = (version_resp.json() or {}).get("version")
            except Exception:
                version = None
            loaded_names: set[str] = set()
            try:
                ps = client.get(f"{_OLLAMA_BASE}/api/ps")
                for m in (ps.json() or {}).get("models", []) or []:
                    name = m.get("name") or m.get("model")
                    if name:
                        loaded_names.add(name)
            except Exception:
                pass

            models = [
                OllamaModelInfo(
                    name=m.get("name") or m.get("model") or "",
                    size_gb=round((m.get("size") or 0) / 1_000_000_000, 2),
                    loaded=(m.get("name") or m.get("model")) in loaded_names,
                    vision=_probe_vision(client, m.get("name") or m.get("model") or ""),
                )
                for m in tags_data.get("models", []) or []
                if m.get("name") or m.get("model")
            ]
    except Exception as exc:
        logger.debug("Ollama not reachable: %s", exc)
        return OllamaInfo(running=False, version=None, models=[])

    return OllamaInfo(running=True, version=version, models=models)


# `details.families` a vision-capable model's own encoder shows up under, on
# an Ollama old enough that `/api/show` doesn't yet report `capabilities`
# (below) — `clip` (llava, granite3.2-vision, minicpm-v: a CLIP vision tower
# bolted onto a text backbone, so it sits *alongside* that backbone's own
# family, e.g. `["qwen2", "clip"]`), `mllama` (llama3.2-vision) and
# `qwen2vl`/`qwen25vl` (the Qwen-VL family, whose vision tower is baked into
# the architecture itself, so it's the *only* family reported). `gemma3` is
# deliberately included even though every Gemma 3 size reports that same
# family name, vision-capable or not (1b has no vision tower, 4b/12b/27b do)
# — harmless here because the *per-model* `capabilities` check below already
# resolves that correctly for any Ollama new enough to report it, and this
# families fallback only ever runs on one old enough that it can't, where a
# false positive on 1b is a smaller mistake than mislabeling three real
# vision models as text-only.
_VISION_FAMILIES = {"clip", "mllama", "qwen2vl", "qwen25vl"}

_VISION_CACHE_TTL = 60.0  # same polling-friendly window as _PROBE_CACHE_TTL below
_vision_cache_lock = threading.Lock()
_vision_cache: dict[str, tuple[float, bool]] = {}


def _probe_vision(client: httpx.Client, name: str) -> bool:
    """Whether Ollama model `name` accepts image input (see N21).

    `/api/system` used to leave this to a name heuristic in the app
    (`app/src/screens/Team.tsx`: a `vl`/`vision`/`-v` infix), which mislabels
    any multimodal model that doesn't spell it in the name — `gemma3:4b`
    read as "modelo de texto local", the same label as a genuinely text-only
    model. Ollama's own `/api/show` knows the truth per *installed* model
    (not per name), so ask it instead: prefer the `capabilities` list current
    Ollama versions report there (`"vision" in capabilities`) — this is what
    correctly tells `gemma3:4b` (vision) apart from a hypothetical `gemma3:1b`
    (no vision tower) despite both reporting `family: "gemma3"` — falling
    back to `details.families` against `_VISION_FAMILIES` on an Ollama old
    enough not to report `capabilities` at all.

    Cached per model name for `_VISION_CACHE_TTL` (the same window the OpenAI
    quota probe uses) since `/api/system` is polled every few seconds and a
    `/api/show` round trip per installed model on every single poll would be
    wasteful — this call is otherwise best-effort like every other probe in
    this module: any failure (network, bad JSON, missing fields) reads as
    "not vision-capable" rather than raising.
    """
    now = time.monotonic()
    with _vision_cache_lock:
        cached = _vision_cache.get(name)
        if cached is not None and (now - cached[0]) < _VISION_CACHE_TTL:
            return cached[1]

    vision = False
    try:
        resp = client.post(f"{_OLLAMA_BASE}/api/show", json={"model": name})
        resp.raise_for_status()
        data = resp.json() or {}
        capabilities = data.get("capabilities")
        if isinstance(capabilities, list):
            vision = "vision" in capabilities
        else:
            families = (data.get("details") or {}).get("families") or []
            vision = any(family in _VISION_FAMILIES for family in families)
    except Exception as exc:
        logger.debug("Could not probe /api/show for %s: %s", name, exc)
        vision = False

    with _vision_cache_lock:
        _vision_cache[name] = (now, vision)
    return vision


# ---------------------------------------------------------------------------
# OpenAI — key presence is free to check; the quota probe is a real, cheap
# call, cached so the system panel polling every few seconds doesn't hammer
# the API or pay for a fresh call each time.
# ---------------------------------------------------------------------------

_PROBE_CACHE_TTL = 3600.0  # 1h — C6: re-probe lazily once a verdict this old
_probe_lock = threading.Lock()
# Keyed by provider id ("openai", "anthropic", …) so every cloud provider's
# probe verdict — not just OpenAI's — survives a page reload instead of
# resetting to "unknown" (see C6). `POST /api/cloud/{provider}/key`
# (server/app.py) writes into this via `cache_probe_verdict` right after it
# probes a freshly-saved key; `host._invalidate_probe_caches` clears the
# whole dict (memory + the persisted file below) when a key is saved/removed
# so a stale verdict for the old key never lingers.
_probe_cache: dict[str, tuple[float, OpenAIStatus]] = {}

# C6: the in-memory cache above is process-lifetime only — a server restart
# (or a second server process) would otherwise re-show "no comprobada" for a
# key that was already validated. Persisted alongside presets.json under the
# same per-user app-support directory so a restart still serves the last
# verdict until it ages out or the key changes.
_PROBE_CACHE_DIR = Path.home() / "Library" / "Application Support" / "2markdown"
_PROBE_CACHE_FILE = _PROBE_CACHE_DIR / "cloud_probe_cache.json"


def _load_persisted_probe_cache() -> dict[str, tuple[float, OpenAIStatus]]:
    if not _PROBE_CACHE_FILE.is_file():
        return {}
    try:
        raw = json.loads(_PROBE_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s: %s", _PROBE_CACHE_FILE, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[float, OpenAIStatus]] = {}
    for provider_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        checked_at = entry.get("checked_at")
        status = entry.get("status")
        if isinstance(checked_at, (int, float)) and isinstance(status, str):
            out[provider_id] = (float(checked_at), status)  # type: ignore[assignment]
    return out


def _save_persisted_probe_cache(cache: dict[str, tuple[float, OpenAIStatus]]) -> None:
    try:
        _PROBE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _PROBE_CACHE_FILE.write_text(
            json.dumps(
                {
                    provider_id: {"checked_at": checked_at, "status": status}
                    for provider_id, (checked_at, status) in cache.items()
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not write %s: %s", _PROBE_CACHE_FILE, exc)


def clear_persisted_probe_cache() -> None:
    """Drop the on-disk verdict cache entirely — called alongside the
    in-memory clear (`host._invalidate_probe_caches`) whenever a key is
    saved or removed, and by `DELETE /api/cloud/{provider}/key` for the
    provider being cleared, so a stale verdict for a changed/removed key
    never survives a restart either."""
    with _probe_lock:
        try:
            if _PROBE_CACHE_FILE.is_file():
                _PROBE_CACHE_FILE.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", _PROBE_CACHE_FILE, exc)


def _dotenv_value(var_name: str) -> str | None:
    """`var_name` from the environment, or from .env without loading it.

    Secrets (config.py) declares no fields, so pydantic-settings never
    populates one; reading .env directly here is read-only and the value
    never gets logged or returned, only whether it is non-empty. Shared by
    every provider probe below (`_dotenv_openai_key` is the OpenAI-specific
    caller kept for its existing call sites) and by `server/models.py`'s
    catalog, so a key's presence is decided in exactly one place.

    Reads the same `.env` path `server/host.py` writes to
    (`host._ENV_PATH`, an absolute repo-root path) rather than a relative
    `Path(".env")` — the two used to disagree the moment cwd wasn't the
    repo root (e.g. a test that patches `host._ENV_PATH` to an isolated
    `tmp_path`), so a key freshly saved by `POST /api/cloud/{provider}/key`
    would still read back as absent here.
    """
    env_value = os.environ.get(var_name)
    if env_value:
        return env_value

    from twomarkdown.server import host

    env_path = host._ENV_PATH
    if not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{var_name}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                return value or None
    except OSError:
        return None
    return None


def _dotenv_openai_key() -> str | None:
    return _dotenv_value("OPENAI_API_KEY")


def key_present(var_name: str) -> bool:
    """Whether `var_name` (e.g. `"ANTHROPIC_API_KEY"`) is set — never the
    value itself, per this module's rule of never logging or returning a
    secret."""
    return _dotenv_value(var_name) is not None


def provider_cloud_info(provider_id: str) -> ProviderCloudInfo:
    """`/api/system`'s entry for a cloud provider.

    `POST /api/cloud/{provider}/key` (server/app.py) already runs one
    best-effort validation call the moment a key is saved
    (`host.probe_cloud_key`) and hands the verdict straight back in that
    response — but until C6 this function then threw that verdict away:
    every later `GET /api/system` (a page reload, most obviously) recomputed
    `status` from key-presence alone, so a key already known to be invalid
    reverted to reading "no comprobada" the moment the page refreshed.

    So: serve the cached verdict (`cache_probe_verdict` /
    `_cached_verdict`) while it is fresh; when it is missing or has aged out
    (`_PROBE_CACHE_TTL`) but a key is present, re-probe lazily here instead
    of guessing `"unknown"`, and cache that result too. Only "no key at all"
    short-circuits straight to `"no_key"` without a network call.
    """
    env_var = _PROVIDER_ENV_VARS[provider_id]
    present = key_present(env_var)
    if not present:
        return ProviderCloudInfo(key_present=False, status="no_key")

    cached = _cached_verdict(provider_id)
    if cached is not None:
        return ProviderCloudInfo(key_present=True, status=cached)

    from twomarkdown.server import host

    key = _dotenv_value(env_var)
    status: OpenAIStatus = host.probe_cloud_key(provider_id, key) if key else "unknown"
    cache_probe_verdict(provider_id, status)
    return ProviderCloudInfo(key_present=True, status=status)


def _probe_openai(key: str) -> OpenAIStatus:
    from twomarkdown.agents.image_ocr import _is_out_of_quota

    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=_PROBE_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("OpenAI probe failed to connect: %s", exc)
        return "unknown"
    if resp.status_code == 200:
        return "ok"
    body = resp.text
    if _is_out_of_quota(RuntimeError(body)):
        return "out_of_credit"
    logger.debug("OpenAI probe returned %s: %s", resp.status_code, body[:200])
    return "unknown"


def cache_probe_verdict(provider_id: str, status: OpenAIStatus) -> None:
    """Record a freshly-probed verdict for `provider_id` (as returned by
    `host.probe_cloud_key`) so the next `GET /api/system` — including one
    from a later page reload or a fresh server process, not just the
    response to the save that triggered the probe — serves it instead of
    recomputing "unknown" from key-presence alone (see C6).

    Timestamped with wall-clock time (`time.time()`), not `monotonic()`,
    because the persisted copy must still make sense to a later process
    that has its own, unrelated monotonic clock origin.
    """
    global _probe_cache
    now = time.time()
    with _probe_lock:
        if _probe_cache is None:
            _probe_cache = {}
        _probe_cache[provider_id] = (now, status)
        persisted = _load_persisted_probe_cache()
        persisted[provider_id] = (now, status)
        _save_persisted_probe_cache(persisted)


def _cached_verdict(provider_id: str) -> OpenAIStatus | None:
    global _probe_cache
    with _probe_lock:
        if _probe_cache is None:
            _probe_cache = {}
        cached = _probe_cache.get(provider_id)
        if cached is None:
            # Not in this process's memory (e.g. right after a restart) —
            # fall back to the persisted verdict a previous process wrote.
            persisted = _load_persisted_probe_cache().get(provider_id)
            if persisted is not None:
                _probe_cache[provider_id] = persisted
                cached = persisted
    if cached is not None and (time.time() - cached[0]) < _PROBE_CACHE_TTL:
        return cached[1]
    return None


def openai_info() -> OpenAIInfo:
    key = _dotenv_openai_key()
    if not key:
        return OpenAIInfo(key_present=False, status="no_key")

    cached = _cached_verdict("openai")
    if cached is not None:
        return OpenAIInfo(key_present=True, status=cached)

    status = _probe_openai(key)
    cache_probe_verdict("openai", status)
    return OpenAIInfo(key_present=True, status=status)


# ---------------------------------------------------------------------------
# genai-prices — its own snapshot date, not a copy this app maintains.
# ---------------------------------------------------------------------------


def prices_info() -> PricesInfo:
    try:
        from genai_prices.data_snapshot import get_snapshot

        snapshot = get_snapshot()
        timestamp = getattr(snapshot, "timestamp", None)
        updated_at = timestamp.isoformat() if timestamp else None
    except Exception as exc:
        logger.debug("genai-prices snapshot unavailable: %s", exc)
        updated_at = None
    return PricesInfo(source="genai-prices", updated_at=updated_at, unknown_models=[])


def get_system_info() -> SystemInfo:
    total_ram = ram_gb()
    return SystemInfo(
        ram_gb=total_ram,
        gpu_limit_gb=gpu_limit_gb(total_ram),
        cpu_cores=cpu_cores(),
        chip=chip_name(),
        tesseract=tesseract_info(),
        ollama=ollama_info(),
        cloud=CloudInfo(
            openai=openai_info(),
            anthropic=provider_cloud_info("anthropic"),
            google=provider_cloud_info("google"),
            groq=provider_cloud_info("groq"),
            mistral=provider_cloud_info("mistral"),
            openrouter=provider_cloud_info("openrouter"),
        ),
        prices=prices_info(),
    )
