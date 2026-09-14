"""Which preset a folder was last converted with (`schemas.FolderPreset`).

Purely a convenience: `POST /api/jobs` (mode `"once"`) records the preset (or
`None`, for a raw `Pipeline` the request sent instead of a `preset_id`) it
just used for a given input path, so the next time that same folder is
inspected (`POST /api/inspect` → `InspectResponse.last_preset_id`) the
Convertir screen can preselect it instead of always defaulting to the first
preset. `GET /api/folders` / `DELETE /api/folders?path=...` expose the same
map for "Equipo y sincronización" to show and forget entries.

This is deliberately not `syncs.py`: a sync already binds
input→output→preset for its own recurring runs and owns that relationship
end to end; this module only remembers the *last* one-off choice for a path,
nothing more, and never reads or writes `syncs.json`.

Persisted as one small JSON file next to `presets.json` — same
one-file-per-store convention as `presets.py`, no migration story needed for
a local single-user server. Capped at `MAX_ENTRIES`: a "remember what I
picked last time" convenience has no business growing without bound, so the
least-recently-used path is dropped once a new one would push it over.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FOLDER_PRESETS_DIR = Path.home() / "Library" / "Application Support" / "2markdown"
FOLDER_PRESETS_FILE = FOLDER_PRESETS_DIR / "folder_presets.json"

MAX_ENTRIES = 200

_lock = threading.Lock()


def _canonical(path: str) -> str:
    """The same resolved-absolute form `batch.estimate.inspect()` hashes into
    `inspect_id` from — so a path recorded here from `POST /api/jobs` and one
    looked up from `POST /api/inspect` agree regardless of `~`, a trailing
    slash, or a relative path the caller happened to pass."""
    return str(Path(path).expanduser().resolve())


def _load_raw() -> dict[str, dict[str, object]]:
    if not FOLDER_PRESETS_FILE.is_file():
        return {}
    try:
        data = json.loads(FOLDER_PRESETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s: %s", FOLDER_PRESETS_FILE, exc)
        return {}


def _save_raw(data: dict[str, dict[str, object]]) -> None:
    FOLDER_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    FOLDER_PRESETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record(path: str, preset_id: str | None) -> None:
    """Remember that `path` was just converted with `preset_id` (`None` for
    a raw `Pipeline`). Best-effort: a write failure here must never fail the
    job it is recording."""
    try:
        key = _canonical(path)
        now = datetime.now(UTC).isoformat()
        with _lock:
            data = _load_raw()
            data[key] = {"preset_id": preset_id, "last_used": now}
            if len(data) > MAX_ENTRIES:
                oldest = min(data, key=lambda k: data[k].get("last_used") or "")
                if oldest != key:
                    del data[oldest]
            _save_raw(data)
    except OSError as exc:
        logger.warning("Could not record folder preset for %s: %s", path, exc)


def get_last_preset_id(path: str) -> str | None:
    """`preset_id` last used for `path` (already-canonical or not), or
    `None` when this path was never converted before or its last job used a
    raw `Pipeline`."""
    with _lock:
        entry = _load_raw().get(_canonical(path))
    return entry.get("preset_id") if entry else None


def list_folders() -> list[dict[str, object]]:
    """Every remembered folder, most recently used first — `GET
    /api/folders`."""
    with _lock:
        data = _load_raw()
    rows = [
        {
            "path": path,
            "preset_id": entry.get("preset_id"),
            "last_used": entry.get("last_used"),
        }
        for path, entry in data.items()
    ]
    rows.sort(key=lambda r: r["last_used"] or "", reverse=True)
    return rows


def delete_folder(path: str) -> bool:
    """Forget `path`; `True` if it was actually there."""
    key = _canonical(path)
    with _lock:
        data = _load_raw()
        if key not in data:
            return False
        del data[key]
        _save_raw(data)
    return True
