"""Host-only actions the frontend calls that aren't part of the formal
contract (`docs/desktop-app.md`'s endpoint table) — `app/src/api/contract.ts`
names them explicitly as "server-side may not exist yet" and degrades to a
visible fallback if a call 404s. They exist here so that fallback never
triggers on a normal Mac.

Nothing here talks to the engine; it's OS/filesystem plumbing:
- `pick_folder` shells out to the same `osascript` a native "Elegir…" button
  would use — no Tauri/native dialog exists yet, so this is the closest
  thing to one from a pure web frontend.
- `pull_ollama_model` reuses `scripts/ollama_host.py` (the same script
  `make build ollama` / `make process` already call) so there is exactly one
  place that knows how to drive the Ollama CLI.
- `save_openai_key` writes `.env` the same way a human editing it from
  `.env_template` would: only the `OPENAI_API_KEY=` line changes, everything
  else in the file (comments, other provider keys) is left alone. The key
  itself is never logged — only whether the write succeeded.
- `open_path`/`reveal_path` shell out to the macOS `open` command (plain, and
  `-R` to reveal in Finder instead of opening) for the desktop queue's "abrir"
  /"mostrar en Finder" actions on a file `GET .../original` said it cannot
  preview inline. Neither checks the path is safe to touch — that's
  `server/jobs.py:is_path_allowed()`'s job (it needs job input/output roots,
  which this module has no reason to know about); these two only run the
  subprocess once a caller (`server/app.py`) has already cleared that gate.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env"
_OLLAMA_HOST_SCRIPT = _REPO_ROOT / "scripts" / "ollama_host.py"

_PULL_TIMEOUT_S = 1800.0  # large vision models can take a while on first pull
_CLOUD_KEY_PROBE_TIMEOUT_S = 5.0


def pick_folder() -> str | None:
    """Native macOS folder picker via `osascript`. Returns `None` (never
    raises) on any other platform, a missing `osascript`, or a user Cancel —
    the frontend's `pickFolder()` already falls back to a pasted path for all
    three."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Elige una carpeta")',
            ],
            capture_output=True,
            text=True,
            timeout=300.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("pick_folder: osascript failed: %s", exc)
        return None
    if result.returncode != 0:
        # Non-zero includes the user clicking Cancel — not an error.
        return None
    path = result.stdout.strip()
    return path or None


def pull_ollama_model(model: str) -> tuple[bool, str | None]:
    """Blocking pull of an Ollama model, run off the event loop by the
    caller (`run_in_threadpool`). Delegates to `scripts/ollama_host.py pull`
    — the same path `make build ollama`/`make process` use — instead of
    duplicating the ensure-then-pull logic here.

    No progress events: the frontend contract's `pullOllamaModel()` returns
    a single `{ok}` once the pull finishes or fails, so this call is just
    slow, not streaming. A `job`-style progress event could reuse
    `twomarkdown.batch.events` later if the UI grows a progress bar for it.
    """
    if not _OLLAMA_HOST_SCRIPT.is_file():
        return False, "ollama_host.py not found"
    try:
        result = subprocess.run(
            [sys.executable, str(_OLLAMA_HOST_SCRIPT), "pull", model],
            capture_output=True,
            text=True,
            timeout=_PULL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"pulling {model} timed out after {int(_PULL_TIMEOUT_S)}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else f"ollama pull {model} failed"
    return True, None


def _invalidate_probe_caches() -> None:
    """Reset every cached provider probe (every provider's, not just
    OpenAI's — see C6) so a freshly saved/removed key is reflected on the
    very next `GET /api/system`/`GET /api/models` instead of up to 60s later
    (`system._PROBE_CACHE_TTL`/`models._CACHE_TTL`)."""
    from twomarkdown.server import models as _models
    from twomarkdown.server import system as _system

    with _system._probe_lock:
        _system._probe_cache.clear()
    _system.clear_persisted_probe_cache()
    with _models._lock:
        _models._cache = None


def _write_env_line(var_name: str, value: str) -> bool:
    """Replace (or append) the `var_name="..."` line in `.env`, leaving every
    other line untouched. Never logs `value`. Returns False only on an I/O
    error — an empty/invalid value is still written and will simply fail the
    next probe, same as a bad key pasted by hand."""
    value = value.strip()
    try:
        existing = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.is_file() else ""
    except OSError as exc:
        logger.warning("_write_env_line(%s): could not read .env: %s", var_name, exc)
        return False

    lines = existing.splitlines()
    new_line = f'{var_name}="{value}"'
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{var_name}="):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)

    try:
        _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("_write_env_line(%s): could not write .env: %s", var_name, exc)
        return False
    return True


def _remove_env_line(var_name: str) -> bool:
    """Drop the `var_name="..."` line from `.env`, if it is there at all —
    every other line untouched. Returns False only on an I/O error; a
    var_name that was never in the file counts as success (there is nothing
    to remove)."""
    try:
        existing = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.is_file() else ""
    except OSError as exc:
        logger.warning("_remove_env_line(%s): could not read .env: %s", var_name, exc)
        return False
    lines = [
        line
        for line in existing.splitlines()
        if not line.strip().startswith(f"{var_name}=")
    ]
    try:
        _ENV_PATH.write_text(
            ("\n".join(lines) + "\n") if lines else "", encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("_remove_env_line(%s): could not write .env: %s", var_name, exc)
        return False
    return True


def save_openai_key(key: str) -> bool:
    """Write `OPENAI_API_KEY` into `.env`, replacing just that line (or
    appending it) so every other secret and comment in the file survives.
    Never logs `key`. Returns False only on an I/O error — an empty/invalid
    key is still written and will simply fail `system.openai_info()`'s next
    probe, same as a bad key pasted by hand."""
    if not _write_env_line("OPENAI_API_KEY", key):
        return False
    _invalidate_probe_caches()
    return True


def save_cloud_key(provider: str, key: str) -> bool:
    """Generalized `save_openai_key`, for every provider in
    `server/models.py:CLOUD_PROVIDER_ENV_VARS` — the env var each provider's
    key lives under is resolved from that same catalog, so the UI never
    needs to know or send it, only the provider id (`"anthropic"`,
    `"google"`, …)."""
    from twomarkdown.server.models import CLOUD_PROVIDER_ENV_VARS

    env_var = CLOUD_PROVIDER_ENV_VARS.get(provider)
    if env_var is None:
        return False
    if not _write_env_line(env_var, key):
        return False
    _invalidate_probe_caches()
    return True


def delete_cloud_key(provider: str) -> bool:
    """Remove a provider's key line from `.env` entirely (not just blank it
    out) — `key_present` for that provider then reads `False` on the very
    next probe, same as if the key had never been set."""
    from twomarkdown.server.models import CLOUD_PROVIDER_ENV_VARS

    env_var = CLOUD_PROVIDER_ENV_VARS.get(provider)
    if env_var is None:
        return False
    if not _remove_env_line(env_var):
        return False
    _invalidate_probe_caches()
    return True


# ---------------------------------------------------------------------------
# Cheap, best-effort key validation — one free-tier-friendly call per
# provider, never more than `_CLOUD_KEY_PROBE_TIMEOUT_S`. "unknown" (never a
# guess) whenever a provider has no such call wired up yet, the call could
# not connect, or it timed out — same rule as every other probe in this app
# (see `server/system.py`'s own module docstring).
# ---------------------------------------------------------------------------


def _quota_exhausted(status_code: int, body: str) -> bool:
    if status_code == 429:
        return True
    lowered = body.lower()
    return any(
        marker in lowered
        for marker in ("insufficient_quota", "quota", "billing", "out of credit")
    )


def _probe_generic_models_endpoint(url: str, headers: dict[str, str]) -> str:
    try:
        resp = httpx.get(url, headers=headers, timeout=_CLOUD_KEY_PROBE_TIMEOUT_S)
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("Cloud key probe failed to connect (%s): %s", url, exc)
        return "unknown"
    if resp.status_code == 200:
        return "ok"
    if resp.status_code in (401, 403):
        return "invalid_key"
    if _quota_exhausted(resp.status_code, resp.text):
        return "out_of_credit"
    logger.debug("Cloud key probe %s returned %s", url, resp.status_code)
    return "unknown"


def probe_cloud_key(provider: str, key: str) -> str:
    """One best-effort validation call for `provider`'s freshly saved `key`.
    Returns an `OpenAIStatus` string (`"ok"`/`"invalid_key"`/
    `"out_of_credit"`/`"unknown"`) — never raises, never logs `key`.

    Caches the verdict (`system.cache_probe_verdict`) so `GET /api/system`
    keeps reporting it — including after a page reload — instead of
    `system.provider_cloud_info` falling back to a guessed `"unknown"` the
    moment this specific response is gone (see C6). `openai` is the one
    provider `system.py` already probes on its own (`openai_info`, on
    `/api/system`'s cadence, not just on save) and caches under the same
    key, so this call's result is folded into that same cache rather than a
    separate one.
    """
    from twomarkdown.server import system as _system

    if provider == "openai":
        status = _system._probe_openai(key)
    elif provider == "anthropic":
        status = _probe_generic_models_endpoint(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    elif provider == "google":
        status = _probe_generic_models_endpoint(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            {},
        )
    elif provider == "groq":
        status = _probe_generic_models_endpoint(
            "https://api.groq.com/openai/v1/models",
            {"Authorization": f"Bearer {key}"},
        )
    elif provider == "mistral":
        status = _probe_generic_models_endpoint(
            "https://api.mistral.ai/v1/models",
            {"Authorization": f"Bearer {key}"},
        )
    elif provider == "openrouter":
        status = _probe_generic_models_endpoint(
            "https://openrouter.ai/api/v1/auth/key",
            {"Authorization": f"Bearer {key}"},
        )
    else:
        status = "unknown"
    _system.cache_probe_verdict(provider, status)
    return status


# ---------------------------------------------------------------------------
# open_path / reveal_path — the desktop queue's "abrir"/"mostrar en Finder"
# actions on a file `GET .../original` said it cannot preview inline. Neither
# checks the path is safe to touch — that's `server/jobs.py:is_path_allowed()`'s
# job (it needs a job's own input/output roots, which this module has no
# reason to know about); `server/app.py` only calls these once that gate has
# already passed.
# ---------------------------------------------------------------------------

_OPEN_TIMEOUT_S = 10.0


def _run_open(args: list[str]) -> str | None:
    if platform.system() != "Darwin":
        return "opening files is only supported on macOS"
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=_OPEN_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "open failed").strip()
    return None


def open_path(path: Path) -> str | None:
    """`open <path>` with the OS default app. Returns an error string, or
    `None` on success."""
    return _run_open(["open", str(path)])


def reveal_path(path: Path) -> str | None:
    """`open -R <path>` — reveal it in Finder instead of opening it."""
    return _run_open(["open", "-R", str(path)])
