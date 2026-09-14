"""Resident-GPU-memory arithmetic for local (Ollama) models.

Two callers ask the same question at different moments and used to answer it
two different ways:

* `twomarkdown/batch/estimate.py` asks it *before* a conversion runs, to show
  the Pipeline editor whether a pipeline's local models fit the GPU at once
  (`_gpu_state`/`EstimateResponse.blocked`/`.warning`).
* `twomarkdown/agents/image_ocr.py` asks it again *during* a conversion, the
  moment it has to actually pick a figure model
  (`effective_figure_model()`).

Before this module existed the engine's answer was a blanket rule ("any two
local models collapse onto one"), while the estimate's answer was already
real arithmetic (`resident_gb` scaled by `RESIDENT_FACTOR`, compared against
`system.gpu_limit_gb()`). That let the UI say a pipeline "fits" (e.g.
qwen2.5vl:7b OCR + gemma3:4b figures, comfortably under a 36 GB GPU limit)
while the engine silently ran figures on the OCR model anyway — the estimate
and the actual conversion disagreeing about what would happen. This module is
the one arithmetic both now share, so they can't drift apart again.

Every model-identity helper this module needs (`is_local_model`,
`normalize_model_id`) lives in `twomarkdown.agents.image_ocr` — and
`image_ocr` itself calls `effective_figure_model()` below. A module-level
`from twomarkdown.agents.image_ocr import ...` here would therefore run into
`image_ocr`'s own half-initialised module during import (whichever module
starts loading second finds the first one incomplete). Every reference to
`image_ocr` below is a function-local import instead, resolved at call time
once both modules are fully loaded — the same pattern
`_ollama_installed_sizes` already used for `twomarkdown.server.system`
before this split, and that `system` import below still uses.
"""

from __future__ import annotations

import re

# Ollama keeps substantially more than a model's file size resident once it
# is actually loaded — KV cache, context buffers, the runtime's own working
# memory. Measured this session on a 48 GB Mac (`ollama ps` while each model
# was loaded, compared against its file size from `ollama list`):
#   qwen2.5vl:32b — 20 GB file, 29.1 GB resident  → ratio ≈ 1.46
#   qwen2.5vl:7b  —  5.6 GB file, 8.8 GB resident → ratio ≈ 1.57
# 1.45 is a round number just under the lower of the two measured ratios: a
# deliberately small overhead estimate, since undercounting only makes a
# pipeline that would not actually fit look like it (still caught once a
# real 32b+7b pair is checked), whereas overcounting would falsely block
# pipelines that would have run fine.
RESIDENT_FACTOR = 1.45

# Ollama's own quantized file size for a local-model family, used only when
# a pipeline names a model Ollama has not actually pulled yet — `/api/tags`
# (`ollama_installed_sizes`) then has no size for it at all, since it only
# lists what is installed.
_FALLBACK_FILE_GB_BY_PARAMS: dict[int, float] = {
    3: 3.2,
    4: 3.3,  # gemma3:4b's quantized default pull size.
    7: 5.6,
    32: 20.0,
    72: 45.0,
}

# The parameter-count suffix Ollama tags spell it with ("qwen2.5vl:32b",
# "llama3.1:7b") — bounded on both sides so "27b" (gemma3:27b, a real and
# differently-sized model) never misreads as containing "7b".
_PARAM_COUNT_RE = re.compile(r"(?<![0-9])(\d+)b(?![0-9a-z])")


def ollama_tag(model: str) -> str:
    """The bare Ollama tag `model` normalizes to ("qwen2.5vl:32b"), stripping
    only the "ollama:" provider prefix `normalize_model_id` adds for a bare
    local name — matches what `/api/tags` (`OllamaModelInfo.name`) reports,
    which never carries that prefix."""
    from twomarkdown.agents.image_ocr import normalize_model_id

    normalized = normalize_model_id(model)
    prefix = "ollama:"
    return normalized[len(prefix) :] if normalized.startswith(prefix) else normalized


def fallback_file_gb(tag: str) -> float | None:
    match = _PARAM_COUNT_RE.search(tag.lower())
    if not match:
        return None
    return _FALLBACK_FILE_GB_BY_PARAMS.get(int(match.group(1)))


def ollama_installed_sizes() -> dict[str, float]:
    """`{tag: file size GB}` for whatever Ollama has actually pulled, right
    now. Best-effort like every probe in `server/system.py`: Ollama not
    running, unreachable, or erroring reads as "nothing installed" rather
    than raising — `resident_gb` then falls back to `fallback_file_gb`."""
    try:
        from twomarkdown.server import system

        info = system.ollama_info()
    except Exception:
        return {}
    if not info.running:
        return {}
    return {m.name: m.size_gb for m in info.models}


def resident_gb(
    model: str | None, installed_sizes: dict[str, float] | None = None
) -> float:
    """GB of GPU memory `model` would occupy once loaded in Ollama.

    A hosted model always costs 0 — it competes for no local VRAM (the whole
    reason `effective_figure_model` below only ever collapses a *local*
    pair). A local model's file size comes from Ollama's own `/api/tags`
    when it is actually installed — pass `installed_sizes` when the caller
    already has it, or leave it to fetch its own — falling back to
    `fallback_file_gb`'s per-parameter-count table for a model the pipeline
    names but Ollama has not pulled yet.
    """
    from twomarkdown.agents.image_ocr import is_local_model

    if not model or not is_local_model(model):
        return 0.0
    tag = ollama_tag(model)
    sizes = installed_sizes if installed_sizes is not None else ollama_installed_sizes()
    size_gb = sizes.get(tag)
    if size_gb is None:
        size_gb = fallback_file_gb(tag)
    if size_gb is None:
        return 0.0
    return round(size_gb * RESIDENT_FACTOR, 2)


def distinct_local_models(*models: str | None) -> list[str]:
    """Local models among `models`, deduplicated by Ollama tag.

    Order-preserving and keyed on the tag rather than the raw string, so
    "qwen2.5vl:32b" and "ollama:qwen2.5vl:32b" collapse to one entry — the
    same model loaded twice (e.g. OCR and a review model that happens to
    agree) costs GPU memory once, not twice.
    """
    from twomarkdown.agents.image_ocr import is_local_model

    seen: dict[str, str] = {}
    for model in models:
        if model and is_local_model(model):
            seen.setdefault(ollama_tag(model), model)
    return list(seen.values())


def gpu_limit_gb() -> float:
    """`system.gpu_limit_gb`, best-effort. 0.0 is a real answer here (a
    non-Apple-Silicon machine, per that function's own docstring), not a
    failure placeholder — but a probe that raises degrades to the same 0.0
    rather than crashing a caller."""
    try:
        from twomarkdown.server import system

        return system.gpu_limit_gb(system.ram_gb())
    except Exception:
        return 0.0


def collapse_message(
    ocr_model: str, figure_model: str, ocr_gb: float, figure_gb: float, limit_gb: float
) -> str:
    """The operator-facing line for a figure model collapsed onto the OCR
    model for lack of GPU room — shown in `events.log` (so the desktop app's
    live log surfaces it) and passed to `logger.warning`.

    `ocr_model`/`figure_model` are full model ids (an Ollama tag or, once
    `normalize_model_id` has run, an `ollama:`-prefixed one) — the structured
    fields the rest of the engine keys off of. This message is prose for a
    human, so it names both models the way the app's comboboxes and preset
    descriptions do: the bare Ollama tag, never the `ollama:` prefix."""
    ocr_display = ollama_tag(ocr_model)
    figure_display = ollama_tag(figure_model)
    return (
        f"Usaré {ocr_display} también para las figuras — {figure_display} no cabe "
        f"junto al OCR ({round(ocr_gb)} + {round(figure_gb)} > {round(limit_gb)} GB)"
    )


def effective_figure_model(
    ocr_model: str, figure_model: str | None, review_model: str | None = None
) -> tuple[str | None, str | None]:
    """The model that actually captions figures, and a collapse message when
    that required substituting it for the OCR model.

    Returns `(resolved_model, collapse_message)`. `collapse_message` is
    `None` whenever nothing was substituted: `figure_model` is empty, either
    model is hosted, or the two are already the same Ollama tag. Only a
    genuinely distinct local pair is even a candidate for collapsing, and
    even then only when their combined `resident_gb` exceeds
    `gpu_limit_gb()` — not on a blanket "two local models" headcount (see
    this module's own docstring for the bug that rule caused: a pipeline the
    UI said "fits" still silently collapsed in the engine).

    `review_model` — when it, too, is local and genuinely distinct from
    `ocr_model` — already occupies GPU memory alongside OCR regardless of
    what happens to the figure model (a review model never shares residency
    the way OCR/figures can), so it counts toward the total the figure model
    is measured against. Leaving it out (M9) made the collapse decision a
    pairwise OCR-vs-figure question when three distinct local models were in
    play: a figure model whose *pair* with OCR alone fit (e.g. 32b OCR +
    gemma3:4b figures, comfortably under the limit) stayed uncollapsed even
    though adding a third resident review model pushed the real total over
    the limit — while a figure model whose pair with OCR alone already
    didn't fit (e.g. 32b OCR + qwen2.5vl:7b figures) still collapsed, by
    coincidence of size rather than by any actual rule about which models
    collapse. Folding `review_model`'s resident size into the comparison
    makes the same three-way arithmetic decide both cases the same way.
    """
    from twomarkdown.agents.image_ocr import is_local_model

    if not figure_model:
        return None, None
    if not (is_local_model(figure_model) and is_local_model(ocr_model)):
        return figure_model, None
    if ollama_tag(figure_model) == ollama_tag(ocr_model):
        return figure_model, None

    limit = gpu_limit_gb()
    sizes = ollama_installed_sizes()
    ocr_gb = resident_gb(ocr_model, sizes)
    figure_gb = resident_gb(figure_model, sizes)
    other_models = distinct_local_models(ocr_model, review_model)
    other_gb = sum(resident_gb(m, sizes) for m in other_models)
    if limit > 0 and (other_gb + figure_gb) > limit:
        message = collapse_message(ocr_model, figure_model, ocr_gb, figure_gb, limit)
        return ocr_model, message
    return figure_model, None
