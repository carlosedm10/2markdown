"""The app's one persisted settings resource.

`docs/desktop-app.md` calls this out explicitly: the first-run wizard is not
a special screen with its own storage — it is *the first edit of Settings*.
Before this module existed, the wizard only held its answers in the React
tree's own state (see `app/src/screens/Wizard.tsx`), so closing the app (or
even just navigating away mid-wizard) silently discarded everything it
asked, and Convertir had nothing to read back — it re-derived its own
defaults instead. This module is the fix: one `Settings` object, GET/PUT at
`/api/settings`, persisted as `settings.json` next to `presets.json` (same
per-user app-support directory, same flat-JSON-file convention as
`presets.py`/`folder_presets.py` — a local single-user server needs no
migration story).

`default_output_for()` is the other half of the fix: it is the *only* place
that turns an input path into a default output path, from either mode.
`InspectResponse.default_output` (server/schemas.py) calls it so the app
never re-derives the sibling/fixed-dir rule client-side, and `POST
/api/jobs` calls it too when `output` is omitted, so a caller that only sends
`pipeline`/`preset_id` still gets the exact same answer Convertir would have
shown it.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from twomarkdown.server.schemas import Settings

logger = logging.getLogger(__name__)

SETTINGS_DIR = Path.home() / "Library" / "Application Support" / "2markdown"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

_lock = threading.Lock()


class SettingsValidationError(ValueError):
    """Raised with a structured `{code, field, message}` triple so `app.py`
    can respond `422` with a body the wizard/Settings screen can switch on
    instead of parsing English prose (same spirit as `jobs.
    UnsavedPageEditError` — see docs/desktop-app.md's retry note)."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


def _load_raw() -> dict[str, object]:
    if not SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s: %s", SETTINGS_FILE, exc)
        return {}


def _save_raw(data: dict[str, object]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _apply_llm_settings(settings: Settings) -> None:
    """Push the settings this resource owns that the engine's own config
    (`twomarkdown.config.llm_config`) needs to know about, right now — not
    only at the next job (`server.jobs._apply_pipeline` does that per-job
    push for `Pipeline` fields; `local_gpu_permits` is not part of a
    `Pipeline`, so nothing else pushes it).

    Called from both `get_settings` and `replace_settings` — the two ends of
    this module's own read/write surface — so a changed
    `local_gpu_permits` takes effect the moment it is next read or saved,
    with no separate server-lifecycle hook to remember to call. Import of
    `twomarkdown.agents.image_ocr` is local to avoid this module (imported
    by `twomarkdown.server.schemas`, hence by `batch/estimate.py`'s own
    `_load_schemas` workaround) becoming one more link in that chain at
    import time.
    """
    from twomarkdown.agents import image_ocr
    from twomarkdown.config import llm_config

    llm_config.local_gpu_permits = settings.local_gpu_permits
    image_ocr.apply_gpu_permits(settings.local_gpu_permits)


def get_settings() -> Settings:
    """The persisted settings, or field defaults (`Settings()`) for whatever
    was never saved yet — including the all-defaults case of a brand-new
    install that has not run the wizard, where `wizard_done` is `False`."""
    with _lock:
        raw = _load_raw()
    try:
        settings = Settings.model_validate(raw)
    except Exception as exc:
        logger.warning("Bad settings.json, using defaults: %s", exc)
        settings = Settings()
    _apply_llm_settings(settings)
    return settings


def _validate(settings: Settings) -> None:
    """The rules `PUT /api/settings` enforces beyond pydantic's own field
    validation — the ones that need to look at *other* fields or at disk/
    preset state, so they can't just be pydantic field validators."""
    if settings.default_output_mode == "fixed":
        if not settings.default_output_dir:
            raise SettingsValidationError(
                "required",
                "default_output_dir",
                "default_output_dir is required when default_output_mode is 'fixed'",
            )
        expanded = Path(settings.default_output_dir).expanduser()
        if not expanded.is_absolute():
            raise SettingsValidationError(
                "not_absolute",
                "default_output_dir",
                f"default_output_dir must be an absolute path (or start with "
                f"~): {settings.default_output_dir!r}",
            )

    # Import here, not at module scope: presets.py imports schemas.py, which
    # this module also sits alongside — a top-level `from twomarkdown.server
    # import presets` risks the same circular-import trap `batch/estimate.py`
    # documents on its own `_load_schemas()` for exactly this package.
    from twomarkdown.server import presets

    if presets.get_preset(settings.default_preset_id) is None:
        raise SettingsValidationError(
            "not_found",
            "default_preset_id",
            f"default_preset_id not found: {settings.default_preset_id!r}",
        )

    if settings.local_gpu_permits not in (1, 2):
        raise SettingsValidationError(
            "out_of_range",
            "local_gpu_permits",
            f"local_gpu_permits must be 1 or 2 (experimental): "
            f"{settings.local_gpu_permits!r}",
        )


def replace_settings(settings: Settings) -> Settings:
    """`PUT /api/settings` — a full replace, validated. Raises
    `SettingsValidationError` (→ 422) rather than persisting a settings
    object that would make `default_output_for()`/job creation fail later for
    reasons the caller can no longer see coming."""
    _validate(settings)
    with _lock:
        _save_raw(settings.model_dump(mode="json"))
    _apply_llm_settings(settings)
    return settings


def default_output_for(input_path: str, settings: Settings | None = None) -> str:
    """Where a conversion of `input_path` lands when the caller names no
    output explicitly — the one rule `InspectResponse.default_output` and a
    `POST /api/jobs` with `output` omitted both defer to, so neither the app
    nor two different server code paths can drift into disagreeing about it.

    * `"sibling"` (default): `"<input>_2markdown"` next to `input_path` —
      works for a single file or a directory alike, since both just need a
      sibling name derived from their own final path component.
    * `"fixed"`: `"<default_output_dir>/<input name>_2markdown"` — every
      conversion lands under one folder the user chose once, named after
      the input so two different inputs don't collide.
    """
    settings = settings if settings is not None else get_settings()
    resolved = Path(input_path).expanduser()
    name = f"{resolved.name}_2markdown"
    if settings.default_output_mode == "fixed":
        # _validate() already refused to persist "fixed" mode without a
        # dir, but a caller that built its own ad-hoc Settings (e.g. a test)
        # could still reach here — fall back to sibling rather than raising
        # out of what is meant to be a pure, always-succeeds resolver.
        if settings.default_output_dir:
            base = Path(settings.default_output_dir).expanduser()
            return str(base / name)
    return str(resolved.parent / name)
