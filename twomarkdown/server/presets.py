"""The three shipped presets, user presets, and builtin overrides.

A preset *is* a saved `Pipeline` (see `docs/desktop-app.md`, "Presets → engine
config"). Builtins are hardcoded here so the app always has something to
convert with on first launch; `PUT /api/presets` can still edit one — that
edit is stored as an *override* keyed by the builtin's id, not a copy of the
whole preset, so "Restablecer" (the client sending back the unmodified
default pipeline) makes the override disappear on its own with no separate
reset endpoint to keep in sync.

Persisted as one JSON file under the platform's per-user app-support
directory — a local single-user server, so a flat file needs no migration
story a real database would.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from twomarkdown.server.schemas import Pipeline, Preset

logger = logging.getLogger(__name__)

PRESETS_DIR = Path.home() / "Library" / "Application Support" / "2markdown"
PRESETS_FILE = PRESETS_DIR / "presets.json"

BUILTIN_PRESETS: list[Preset] = [
    Preset(
        id="rapido",
        name="Rápido",
        builtin=True,
        pipeline=Pipeline(
            ocr_model="tesseract",
            figure_model=None,
            review_model=None,
            workers=4,
            describe_figures=False,
            clean=True,
            tables=True,
            emit_chunks=False,
            ocr_dpi=300,
        ),
    ),
    Preset(
        id="apuntes-a-mano",
        name="Apuntes a mano",
        builtin=True,
        pipeline=Pipeline(
            ocr_model="ollama:qwen2.5vl:7b",
            figure_model="ollama:qwen2.5vl:7b",
            review_model="openai:gpt-4o-mini",
            workers=1,
            describe_figures=True,
            clean=True,
            tables=True,
            emit_chunks=False,
            ocr_dpi=300,
        ),
    ),
    Preset(
        id="archivo-grande",
        name="Archivo grande",
        builtin=True,
        pipeline=Pipeline(
            # Same "sin IA" text pipeline as Rápido — the trade-off this
            # preset offers is throughput over a big batch, not model choice:
            # more files in flight at once, lower render DPI (still legible
            # for printed text, just cheaper per page) and chunked output
            # since a large-file run is exactly the case a downstream
            # pipeline wants pre-split Markdown for (see N18 — until this
            # change the two presets were byte-identical and their card blurb
            # correctly, uselessly, said so).
            ocr_model="tesseract",
            figure_model=None,
            review_model=None,
            workers=8,
            describe_figures=False,
            clean=True,
            tables=True,
            emit_chunks=True,
            ocr_dpi=200,
        ),
    ),
]

_BUILTIN_BY_ID = {p.id: p for p in BUILTIN_PRESETS}

_lock = threading.Lock()


def _load_raw() -> dict[str, object]:
    if not PRESETS_FILE.is_file():
        return {}
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s: %s", PRESETS_FILE, exc)
        return {}


def _save_raw(data: dict[str, object]) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_presets() -> list[Preset]:
    """Builtins (with any stored override applied), then user presets."""
    with _lock:
        raw = _load_raw()
    overrides = raw.get("builtin_overrides") or {}
    user_raw = raw.get("user_presets") or []

    presets: list[Preset] = []
    for builtin in BUILTIN_PRESETS:
        override = overrides.get(builtin.id)
        if override:
            try:
                presets.append(
                    Preset(
                        id=builtin.id,
                        name=builtin.name,
                        builtin=True,
                        pipeline=Pipeline.model_validate(override),
                    )
                )
                continue
            except Exception as exc:
                logger.warning(
                    "Bad override for %s, using default: %s", builtin.id, exc
                )
        presets.append(builtin)

    for entry in user_raw:
        try:
            presets.append(Preset.model_validate(entry))
        except Exception as exc:
            logger.warning("Dropping malformed user preset: %s", exc)

    return presets


def get_preset(preset_id: str) -> Preset | None:
    for preset in list_presets():
        if preset.id == preset_id:
            return preset
    return None


def _normalized_pipeline(pipeline: Pipeline) -> Pipeline:
    """`pipeline` with its model-id fields put through the same
    normalization the engine applies (`agents.image_ocr.normalize_model_id`).

    Compared instead of the raw pipeline so a spelling difference alone
    (a bare local model name like "qwen2.5vl:7b" against the same model
    stored with its "ollama:" prefix, or vice versa) never reads as "this
    builtin was changed" — see N12: a wizard run once wrote back a builtin
    override that only differed from the default in exactly this kind of
    normalization, not in any field a user actually edited.
    """
    from twomarkdown.agents.image_ocr import normalize_model_id

    def _norm(model: str | None) -> str | None:
        return normalize_model_id(model) if model else model

    return pipeline.model_copy(
        update={
            "ocr_model": _norm(pipeline.ocr_model),
            "figure_model": _norm(pipeline.figure_model),
            "review_model": _norm(pipeline.review_model),
        }
    )


def _upsert_into(
    overrides: dict[str, dict], user_by_id: dict[str, dict], preset: Preset
) -> None:
    """Shared upsert step for one preset, used by both `replace_all` and
    `merge_upsert` -- only how the *rest* of the store is seeded (full state
    vs. what was already on disk) differs between them."""
    builtin = _BUILTIN_BY_ID.get(preset.id)
    if builtin is not None:
        if _normalized_pipeline(preset.pipeline) != _normalized_pipeline(
            builtin.pipeline
        ):
            overrides[preset.id] = preset.pipeline.model_dump(mode="json")
        else:
            overrides.pop(preset.id, None)
        return
    user_by_id[preset.id] = preset.model_dump(mode="json")


def replace_all(presets: list[Preset]) -> list[Preset]:
    """`PUT /api/presets` -- FULL replace, as documented in
    `docs/desktop-app.md`: the request is the entire desired state. A
    builtin or user preset that existed before and is not mentioned in
    `presets` is gone afterwards (a builtin reverts to its hardcoded
    default, same as an explicit "Restablecer"; a user preset is deleted
    outright).

    A builtin preset whose pipeline matches its hardcoded default drops any
    stored override (this *is* "Restablecer"); one that differs is stored as
    an override. Both sides are compared *normalized* (`_normalized_pipeline`)
    so an untouched builtin sent back exactly as `GET /api/presets` returned
    it never creates a spurious override (see N12).

    This *used* to also be the only write endpoint, and every app-side
    caller only ever sent the preset(s) that actually changed (see
    `app/src/lib/presets.ts`, `changedPresets`) -- which under a full-replace
    contract silently deleted every other user preset the moment a caller
    sent fewer than the complete list (C7). That is now `PATCH
    /api/presets`'s job (`merge_upsert`, upsert-only, never deletes); PUT
    keeps replace semantics because something has to, for the one legitimate
    case a merge can't do -- dropping a preset the caller no longer wants,
    alongside a matching `DELETE /api/presets/{id}` for a single id. Since a
    client bug of exactly this shape has already shipped once, any PUT that
    is about to make an *existing user preset* disappear logs a warning
    naming it, so the mistake stays visible in the technical log even though
    the drop itself is this endpoint's documented, intended behaviour.
    """
    with _lock:
        raw = _load_raw()
        prior_user_ids = {
            entry.get("id")
            for entry in (raw.get("user_presets") or [])
            if isinstance(entry, dict) and entry.get("id")
        }

        overrides: dict[str, dict] = {}
        user_by_id: dict[str, dict] = {}
        for preset in presets:
            _upsert_into(overrides, user_by_id, preset)

        dropped = sorted(prior_user_ids - set(user_by_id))
        if dropped:
            logger.warning(
                "PUT /api/presets: %d user preset(s) not present in this "
                "request will be deleted (full replace, see C7): %s",
                len(dropped),
                ", ".join(dropped),
            )

        _save_raw(
            {
                "builtin_overrides": overrides,
                "user_presets": list(user_by_id.values()),
            }
        )
    return list_presets()


def merge_upsert(presets: list[Preset]) -> list[Preset]:
    """`PATCH /api/presets` -- upserts every preset in `presets` into
    whatever is already on disk; never deletes. A builtin or user preset
    that already exists and is simply not mentioned survives untouched --
    the merge semantics the app-side callers actually need (see C7):
    editing/saving one preset must never make an unrelated saved preset
    disappear. Use `DELETE /api/presets/{id}` for the one case this can't
    express, removing a preset on purpose."""
    with _lock:
        raw = _load_raw()
        overrides: dict[str, dict] = dict(raw.get("builtin_overrides") or {})
        user_by_id: dict[str, dict] = {
            entry.get("id"): entry
            for entry in (raw.get("user_presets") or [])
            if isinstance(entry, dict) and entry.get("id")
        }

        for preset in presets:
            _upsert_into(overrides, user_by_id, preset)

        _save_raw(
            {
                "builtin_overrides": overrides,
                "user_presets": list(user_by_id.values()),
            }
        )
    return list_presets()


def delete_preset(preset_id: str) -> bool:
    """`DELETE /api/presets/{id}` -- explicit removal. Only meaningful for a
    user preset (a builtin has no "gone" state of its own -- the closest
    equivalent, dropping a stored override to revert it to its hardcoded
    default, already happens automatically as part of `replace_all`/
    `merge_upsert` whenever the sent pipeline matches the default). Returns
    False, so the caller can 404, when `preset_id` names a builtin or an id
    that was not a stored user preset to begin with."""
    if preset_id in _BUILTIN_BY_ID:
        return False
    with _lock:
        raw = _load_raw()
        user_raw = [
            entry
            for entry in (raw.get("user_presets") or [])
            if isinstance(entry, dict)
        ]
        remaining = [entry for entry in user_raw if entry.get("id") != preset_id]
        if len(remaining) == len(user_raw):
            return False
        _save_raw(
            {
                "builtin_overrides": raw.get("builtin_overrides") or {},
                "user_presets": remaining,
            }
        )
    return True
