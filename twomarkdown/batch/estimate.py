"""Time and cost, per stage, before a batch is converted.

Where `planner.py` orders a batch by weight, this module answers the question
the desktop app actually shows the user: how long, and how much, broken down
by stage and model — before anything runs. Two things make that answerable
without running the model first:

* **Image tokens are deterministic.** A vision provider bills by the pixels it
  is handed, not by what it writes back, so the *input* side of an OCR or
  figure call can be computed from a page's rendered size and the provider's
  own tiling rule (see `estimate_image_tokens`). Only the *output* — how much
  the model writes — is genuinely unknown, and is assumed from measured
  averages, same as `planner.py` assumes seconds.
* **A price table already exists.** `genai-prices` (a pydantic-ai dependency)
  turns tokens into dollars for any model it recognises, and this module never
  starts its background updater — it reads whatever snapshot is bundled or
  already loaded, so an estimate never blocks on the network. A model it does
  not recognise costs `None`, not `$0`; a user-supplied manual price is tried
  first, so a brand-new or self-hosted model still gets a real number.

The scheduling arithmetic mirrors the desktop-app mockup's Pipeline simulator
(`docs/mockups/desktop-v4.html`), which is itself a small model of the real
concurrency in `agents/image_ocr.py`: one shared permit for every local model
(`_page_ocr_lock`), up to four permits for hosted APIs (`_remote_lock`), and
OCR and figures sharing one resident model only when they must —
`_effective_figure_model` (mirroring `agents.image_ocr.effective_figure_model`)
reuses the OCR model for figures only when the two are local, genuinely
distinct, and would not both fit resident; two local models that do fit stay
distinct, each costing its own share of GPU memory.

A local, text-only `review_model` is never collapsed the same way — it can
always name a second (or, when figures did not collapse, third) local model
distinct from OCR/figures. Whether the whole set actually fits is arithmetic,
not a headcount: `twomarkdown.batch.gpu_memory.resident_gb` scales each
model's installed (or, if not yet pulled, per-parameter-count-estimated)
file size by `RESIDENT_FACTOR` and `_gpu_state` compares the sum against
`system.gpu_limit_gb` (~75% of RAM on Apple Silicon) — see that function's
docstring for the measurements the factor comes from and the 32b+7b example
that motivated it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import fitz

from twomarkdown.agents.image_ocr import (
    is_local_model,
    model_provider,
    normalize_model_id,
)
from twomarkdown.batch import gpu_memory
from twomarkdown.batch.gpu_memory import resident_gb
from twomarkdown.batch.planner import _local_rates
from twomarkdown.batch.walker import discover_files
from twomarkdown.config import (
    DEFAULT_INCLUDE_EXTENSIONS,
    IWORK_BUNDLE_SUFFIXES,
    figure_config,
    llm_config,
    pdf_ocr_config,
)

_SCHEMAS_MODULE = "twomarkdown.server.schemas"


def _load_schemas():
    """`twomarkdown.server.schemas`, however the rest of `twomarkdown.server`
    is doing today.

    That package's own `__init__` wires the local desktop-app FastAPI server,
    which drags in every route module — one of which, as of this writing, has
    a circular import that makes the plain `from twomarkdown.server.schemas
    import ...` fail with an unrelated ImportError. `schemas.py` itself is
    pydantic-only with no package-relative imports, so this only needs to
    reach that one file, not stand up the server. It always returns the same
    module object other importers already hold: it is the single source of
    truth for this contract (see the module's own docstring), and pydantic
    validation treats two separately-executed copies of the same class as
    incompatible types.
    """
    cached = sys.modules.get(_SCHEMAS_MODULE)
    if cached is not None:
        return cached
    try:
        import twomarkdown.server.schemas as schemas_module

        return schemas_module
    except ImportError:
        # The failed attempt above may still have cached schemas.py in
        # sys.modules before whatever else in the package broke — reuse that
        # rather than re-executing the file into a second, incompatible copy.
        cached = sys.modules.get(_SCHEMAS_MODULE)
        if cached is not None:
            return cached

    schemas_path = Path(__file__).resolve().parents[1] / "server" / "schemas.py"
    spec = importlib.util.spec_from_file_location(_SCHEMAS_MODULE, schemas_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SCHEMAS_MODULE] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_schemas = _load_schemas()
BlockedInfo = _schemas.BlockedInfo
Bottleneck = _schemas.Bottleneck
EstimateAlternative = _schemas.EstimateAlternative
EstimateResponse = _schemas.EstimateResponse
EstimateStage = _schemas.EstimateStage
FileKind = _schemas.FileKind
InspectFile = _schemas.InspectFile
InspectResponse = _schemas.InspectResponse
InspectTotals = _schemas.InspectTotals
Pipeline = _schemas.Pipeline
StageKey = _schemas.StageKey
StageKind = _schemas.StageKind
WarningInfo = _schemas.WarningInfo

# ---------------------------------------------------------------------------
# Image-token billing rules
# ---------------------------------------------------------------------------

# OpenAI's "high detail" tiling: fit the image inside 2048x2048, shrink the
# shortest side to 768, then bill 170 tokens per 512px tile plus a flat 85.
# https://platform.openai.com/docs/guides/vision — never scales an image up,
# only down, at each step.
_OPENAI_FIT_PX = 2048
_OPENAI_SHORT_SIDE_PX = 768
_OPENAI_TILE_PX = 512
_OPENAI_TOKENS_PER_TILE = 170
_OPENAI_BASE_TOKENS = 85

# Qwen2.5-VL patches the image into 28x28 blocks (one visual token each) after
# "smart resize" nudges each side to a multiple of 28 and clamps the total
# pixel count to a min/max window. Values are Qwen2VLImageProcessor's
# documented defaults (min_pixels=4*28*28, max_pixels=16384*28*28); a model
# whose own window differs would need overriding these, but no such override
# is wired here yet, so this is the one window this module knows.
_QWEN_PATCH_PX = 28
_QWEN_MIN_PIXELS = 4 * _QWEN_PATCH_PX * _QWEN_PATCH_PX
_QWEN_MAX_PIXELS = 16384 * _QWEN_PATCH_PX * _QWEN_PATCH_PX


def _provider_for_tokens(model: str) -> str | None:
    """Which tiling rule applies, or None when this module has none for it."""
    if not model:
        return None
    if model_provider(model) == "openai":
        return "openai"
    name = normalize_model_id(model).lower()
    if "qwen2.5-vl" in name or "qwen2.5vl" in name or "qwen2_5_vl" in name:
        return "qwen"
    return None


def _openai_image_tokens(width_px: float, height_px: float) -> int:
    scale = min(1.0, _OPENAI_FIT_PX / width_px, _OPENAI_FIT_PX / height_px)
    width_px, height_px = width_px * scale, height_px * scale

    shortest = min(width_px, height_px)
    if shortest > _OPENAI_SHORT_SIDE_PX:
        shrink = _OPENAI_SHORT_SIDE_PX / shortest
        width_px, height_px = width_px * shrink, height_px * shrink

    tiles_w = math.ceil(width_px / _OPENAI_TILE_PX)
    tiles_h = math.ceil(height_px / _OPENAI_TILE_PX)
    return tiles_w * tiles_h * _OPENAI_TOKENS_PER_TILE + _OPENAI_BASE_TOKENS


def _qwen_smart_resize(width_px: float, height_px: float) -> tuple[float, float]:
    """Nudge (w, h) to a multiple of 28 inside Qwen's min/max pixel window.

    Ported from the `smart_resize` reference in `qwen-vl-utils` — rounding to
    the patch size first, then scaling the *whole* box (not just clamping) so
    the aspect ratio survives the min/max correction.
    """
    factor = _QWEN_PATCH_PX
    h_bar = max(factor, round(height_px / factor) * factor)
    w_bar = max(factor, round(width_px / factor) * factor)
    if h_bar * w_bar > _QWEN_MAX_PIXELS:
        beta = math.sqrt((height_px * width_px) / _QWEN_MAX_PIXELS)
        h_bar = math.floor(height_px / beta / factor) * factor
        w_bar = math.floor(width_px / beta / factor) * factor
    elif h_bar * w_bar < _QWEN_MIN_PIXELS:
        beta = math.sqrt(_QWEN_MIN_PIXELS / (height_px * width_px))
        h_bar = math.ceil(height_px * beta / factor) * factor
        w_bar = math.ceil(width_px * beta / factor) * factor
    return max(factor, w_bar), max(factor, h_bar)


def _qwen_image_tokens(width_px: float, height_px: float) -> int:
    w_bar, h_bar = _qwen_smart_resize(width_px, height_px)
    return int((w_bar / _QWEN_PATCH_PX) * (h_bar / _QWEN_PATCH_PX))


def estimate_image_tokens(width_px: float, height_px: float, model: str) -> int | None:
    """Vision tokens one rendered image costs, by its provider's own rule.

    Only the input side is estimated — the only side a provider bills on
    pixels alone. A provider whose tiling rule is not implemented here (or a
    non-vision "model" like `tesseract`) returns None, never a guess.
    """
    if width_px <= 0 or height_px <= 0:
        return None
    provider = _provider_for_tokens(model)
    if provider == "openai":
        return _openai_image_tokens(width_px, height_px)
    if provider == "qwen":
        return _qwen_image_tokens(width_px, height_px)
    return None


# ---------------------------------------------------------------------------
# inspect() — cheap per-file counts, reusing the engine's own heuristics
# ---------------------------------------------------------------------------

_IMAGE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".svg",
    }
)
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


def _classify_kind(suffix: str) -> FileKind:
    if suffix == ".pdf":
        # A PDF's real kind depends on its scanned ratio; see _inspect_pdf.
        return "text"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _OFFICE_SUFFIXES:
        return "office"
    if suffix in DEFAULT_INCLUDE_EXTENSIONS:
        return "text"
    return "other"


def _inspect_pdf(path: Path, size: int) -> InspectFile:
    """Count pages, scanned pages, figures and text chars in one PyMuPDF pass.

    "Scanned" reuses `pdf_ocr.should_fallback`'s own per-page test (too little
    text, or a scrambled text layer) rather than re-deriving it, so this module
    can never drift from what the converter will actually decide to OCR.
    """
    from twomarkdown.converter.figures import detect_figure_regions
    from twomarkdown.converter.pdf_ocr import is_scrambled_text

    try:
        doc = fitz.open(path)
    except Exception:
        return InspectFile(path=str(path), kind="other", bytes=size)

    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    scanned = figures = text_chars = 0
    try:
        for page in doc:
            try:
                text = page.get_text().strip()
            except Exception:
                text = ""
            text_chars += len(text)
            if len(text) < min_chars or is_scrambled_text(text):
                scanned += 1
            if figure_config.figures_enabled:
                try:
                    figures += len(detect_figure_regions(page))
                except Exception:
                    pass  # figure detection is best-effort for an estimate
        pages = doc.page_count
    finally:
        doc.close()

    kind: FileKind = "scanned" if scanned * 2 > pages else "text"
    return InspectFile(
        path=str(path),
        kind=kind,
        pages=pages,
        scanned_pages=scanned,
        figures=figures,
        text_chars=text_chars,
        bytes=size,
    )


def _inspect_file(path: Path) -> InspectFile:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if path.suffix.lower() == ".pdf":
        return _inspect_pdf(path, size)
    return InspectFile(
        path=str(path), kind=_classify_kind(path.suffix.lower()), bytes=size
    )


def inspect(path: str | Path) -> InspectResponse:
    """Walk `path` and count the model-bound work in it, cheaply.

    A single file is inspected on its own; a directory is walked with the same
    `discover_files` the batch itself uses, so an estimate only ever covers
    files that would actually be converted.
    """
    resolved = Path(path)
    if resolved.is_dir():
        # No output directory exists yet at inspect time; point at a sibling
        # that cannot itself be inside the input tree, so nothing is excluded.
        fake_output = resolved.parent / f".{resolved.name}.inspect-out"
        files = discover_files(resolved, fake_output)
    elif resolved.is_file():
        files = [resolved]
    else:
        raise FileNotFoundError(str(path))

    inspected = [_inspect_file(f) for f in files]
    totals = InspectTotals(
        files=len(inspected),
        pages=sum(f.pages for f in inspected),
        scanned_pages=sum(f.scanned_pages for f in inspected),
        figures=sum(f.figures for f in inspected),
        bytes=sum(f.bytes for f in inspected),
    )
    digest = hashlib.sha1(str(resolved.resolve()).encode("utf-8")).hexdigest()
    last_preset_id: str | None = None
    try:
        from twomarkdown.server import folder_presets

        last_preset_id = folder_presets.get_last_preset_id(str(resolved))
    except Exception:
        # Preset history is a convenience, never a reason inspection itself
        # should fail — same spirit as every other best-effort probe here.
        pass
    default_output = str(resolved.parent / f"{resolved.name}_2markdown")
    try:
        from twomarkdown.server import settings as settings_module

        default_output = settings_module.default_output_for(str(resolved))
    except Exception:
        # Same best-effort spirit as last_preset_id above — a broken
        # settings.json must never make inspection itself fail; the sibling
        # fallback above is exactly what default_output_for()'s own default
        # ("sibling") mode already computes.
        pass
    return InspectResponse(
        inspect_id=digest[:16],
        root=str(resolved),
        files=inspected,
        totals=totals,
        last_preset_id=last_preset_id,
        default_output=default_output,
    )


# ---------------------------------------------------------------------------
# Manual prices — consulted before genai-prices
# ---------------------------------------------------------------------------

_MANUAL_PRICES_FILENAME = "manual_prices.json"


def _default_config_dir() -> Path:
    """~/Library/Application Support/2markdown — Mac only, platformdirs is not
    a dependency and this app is Mac-first (see AGENTS.md / docs/README.md)."""
    return Path.home() / "Library" / "Application Support" / "2markdown"


def manual_prices_path(config_dir: Path | None = None) -> Path:
    return (config_dir or _default_config_dir()) / _MANUAL_PRICES_FILENAME


def load_manual_prices(
    config_dir: Path | None = None,
) -> dict[str, dict[str, float]]:
    path = manual_prices_path(config_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_manual_price(
    model: str,
    input_per_mtok: float,
    output_per_mtok: float,
    *,
    config_dir: Path | None = None,
) -> None:
    """Remember a $/Mtok price the user typed in, when genai-prices has none."""
    path = manual_prices_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_manual_prices(config_dir)
    data[model] = {
        "input_per_mtok": input_per_mtok,
        "output_per_mtok": output_per_mtok,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cloud_price(
    model: str, input_tokens: int, output_tokens: int, *, config_dir: Path | None = None
) -> tuple[float | None, bool]:
    """USD for one cloud call, or (None, True) when no price is known.

    A manual price wins over genai-prices — it exists specifically for the
    models genai-prices does not have yet. Never touches the network: this
    reads whichever snapshot genai-prices already has bundled or loaded.
    """
    manual = load_manual_prices(config_dir).get(model)
    if manual:
        usd = (input_tokens / 1_000_000) * manual["input_per_mtok"] + (
            output_tokens / 1_000_000
        ) * manual["output_per_mtok"]
        return usd, False

    try:
        from genai_prices import Usage, calc_price
    except Exception:
        return None, True

    model_ref = normalize_model_id(model).split(":", 1)[-1]
    try:
        result = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model_ref,
            provider_id=model_provider(model),
        )
    except Exception:
        # genai-prices raises LookupError for an unrecognised model/provider —
        # caught broadly because a lookup miss should never crash an estimate.
        return None, True
    return float(result.total_price), False


# ---------------------------------------------------------------------------
# estimate() — per-stage time and cost, then the scheduling rules
# ---------------------------------------------------------------------------

REMOTE_SECONDS_PER_PAGE_OCR = 8.0
REMOTE_SECONDS_PER_FIGURE = 5.0
REMOTE_SECONDS_PER_PAGE_REVIEW = 3.0
CPU_SECONDS_PER_PAGE = 2.0  # Tesseract

# Review has no measured local rate of its own (see LLMConfig.review_model's
# docstring: it "competes for no local VRAM" by design, so it is rarely
# local). This is a judgment call, not a measurement: lighter than a
# transcription page, because it reads and writes text only, never an image.
LOCAL_SECONDS_PER_REVIEW_PAGE = 5.0

# The page the planner's LOCAL_SECONDS_PER_* constants were measured against
# (config.py: pdf_ocr_dpi=300 fitted to llm_ocr_max_dimension=2200, on a
# typical 8.5x11in page). Any other render size is scaled relative to this by
# its own token count, so a smaller or cropped image costs proportionally less
# local GPU time.
REFERENCE_PAGE_WIDTH_PX = 2200
REFERENCE_PAGE_HEIGHT_PX = 1650

OUTPUT_TOKENS_PER_OCR_PAGE = 600
OUTPUT_TOKENS_PER_FIGURE = 150
# No transcription exists yet at estimate time to measure its length from, so
# this assumes a middling prose page — long enough that the estimate is not
# trivially $0, short enough not to overstate a proofreading pass that only
# ever edits characters (see page_review.py).
REVIEW_ASSUMED_CHARS_PER_PAGE = 2000

EXTRACT_SECONDS_PER_PAGE = 0.1
WRITE_SECONDS_PER_PAGE = 0.05


def _stage_kind(model: str | None) -> StageKind:
    if not model:
        return "none"
    if model == "tesseract":
        return "cpu"
    return "gpu" if is_local_model(model) else "cloud"


def _first_page_points(path: Path) -> tuple[float, float] | None:
    """A PDF's first page size in points — read, never rendered."""
    try:
        doc = fitz.open(path)
    except Exception:
        return None
    try:
        if doc.page_count == 0:
            return None
        rect = doc[0].rect
        return rect.width, rect.height
    except Exception:
        return None
    finally:
        doc.close()


def _dpi_and_cap(
    width_pt: float, height_pt: float, *, dpi: int, max_dimension: int
) -> tuple[float, float]:
    """The pixel size a `width_pt`x`height_pt` box renders at, capped like
    `image_prep.prepare_image_for_vision_llm` caps the actual OCR image."""
    scale = dpi / 72.0
    width_px, height_px = width_pt * scale, height_pt * scale
    longest = max(width_px, height_px)
    if max_dimension > 0 and longest > max_dimension:
        shrink = max_dimension / longest
        width_px, height_px = width_px * shrink, height_px * shrink
    return width_px, height_px


def _representative_pdf(files: list[InspectFile]) -> Path | None:
    for f in files:
        if f.pages > 0 and Path(f.path).suffix.lower() == ".pdf":
            return Path(f.path)
    return None


def _ocr_render_dimensions(files: list[InspectFile]) -> tuple[float, float] | None:
    path = _representative_pdf(files)
    if path is None:
        return None
    points = _first_page_points(path)
    if points is None:
        return None
    return _dpi_and_cap(
        *points,
        dpi=pdf_ocr_config.pdf_ocr_dpi,
        max_dimension=llm_config.llm_ocr_max_dimension,
    )


# A figure's real bbox is only known once figure detection has actually run
# against a rendered page (see converter/figures.py), which an estimate does
# not do. Half the page in each dimension approximates a typical plot or
# diagram crop — not a full-page scan, not a sliver of it.
_FIGURE_BOX_FRACTION = 0.5


def _figure_render_dimensions(files: list[InspectFile]) -> tuple[float, float] | None:
    path = _representative_pdf(files)
    if path is None:
        return None
    points = _first_page_points(path)
    if points is None:
        return None
    width_pt, height_pt = points
    return _dpi_and_cap(
        width_pt * _FIGURE_BOX_FRACTION,
        height_pt * _FIGURE_BOX_FRACTION,
        dpi=figure_config.figure_dpi,
        max_dimension=llm_config.llm_figure_max_dimension,
    )


def _local_image_seconds(
    base_rate: float, count: int, dims: tuple[float, float] | None, model: str
) -> float:
    """`base_rate * count`, scaled by how this page's tokens compare to the
    reference page the rate was measured against — see REFERENCE_PAGE_*."""
    if count <= 0:
        return 0.0
    if dims is None:
        return count * base_rate
    tokens = estimate_image_tokens(dims[0], dims[1], model)
    reference = estimate_image_tokens(
        REFERENCE_PAGE_WIDTH_PX, REFERENCE_PAGE_HEIGHT_PX, model
    )
    if not tokens or not reference:
        return count * base_rate
    return count * base_rate * (tokens / reference)


def _image_stage_price(
    model: str,
    dims: tuple[float, float] | None,
    count: int,
    output_tokens_per_unit: int,
) -> tuple[float | None, bool]:
    if count <= 0:
        return 0.0, False
    if dims is None:
        return None, True
    tokens = estimate_image_tokens(dims[0], dims[1], model)
    if tokens is None:
        return None, True
    return _cloud_price(model, tokens * count, output_tokens_per_unit * count)


def _stage(
    key: StageKey,
    model: str | None,
    kind: StageKind,
    seconds: float,
    usd: float | None,
    unknown_price: bool = False,
    *,
    effective_model: str | None = None,
    note: str | None = None,
) -> EstimateStage:
    """Thin constructor so no call site below has to wrap across lines."""
    return EstimateStage(
        key=key,
        model=model,
        kind=kind,
        seconds=seconds,
        usd=usd,
        unknown_price=unknown_price,
        effective_model=effective_model,
        note=note,
    )


def _ocr_stage(
    model: str, scanned_pages: int, dims: tuple[float, float] | None
) -> EstimateStage:
    kind = _stage_kind(model)
    if scanned_pages <= 0:
        return _stage("ocr", model, kind, 0.0, 0.0)
    if kind == "cpu":
        return _stage("ocr", model, kind, scanned_pages * CPU_SECONDS_PER_PAGE, 0.0)
    if kind == "gpu":
        # Per-model rate (see planner._LOCAL_SECONDS_PER_VLM_PAGE_BY_MODEL) —
        # not the flat `LOCAL_SECONDS_PER_VLM_PAGE`, which was measured on
        # qwen2.5vl:32b and overshoots a smaller local model like
        # qwen2.5vl:7b by ~3x (see N8: the wizard already advertises
        # ~40s/página for it, and this used to still quote ~3min).
        page_rate, _ = _local_rates(model)
        seconds = _local_image_seconds(page_rate, scanned_pages, dims, model)
        return _stage("ocr", model, kind, seconds, 0.0)
    seconds = scanned_pages * REMOTE_SECONDS_PER_PAGE_OCR
    usd, unknown = _image_stage_price(
        model, dims, scanned_pages, OUTPUT_TOKENS_PER_OCR_PAGE
    )
    return _stage("ocr", model, kind, seconds, usd, unknown)


def _figures_stage(
    model: str | None,
    figure_count: int,
    dims: tuple[float, float] | None,
    *,
    enabled: bool,
    note: str | None = None,
) -> EstimateStage:
    """`model` is already the *effective* figure model — the caller has run
    it through `gpu_memory.effective_figure_model`, so `seconds`/`usd` below
    are always priced for what will really run. `note` is that same call's
    collapse message (`None` when nothing was substituted); whenever it is
    set, `effective_model` echoes `model` so a client can show both "what I
    asked for" (its own copy of `pipeline.figure_model`) and "what will
    actually run" from one stage object, without having to parse `note`."""
    if not enabled or not model:
        return _stage("figures", model, "none", 0.0, 0.0)
    kind = _stage_kind(model)
    if figure_count <= 0 or kind == "cpu":  # figures are never captioned by Tesseract
        return _stage("figures", model, kind, 0.0, 0.0)
    effective_model = model if note else None
    if kind == "gpu":
        _, figure_rate = _local_rates(model)
        seconds = _local_image_seconds(figure_rate, figure_count, dims, model)
        return _stage(
            "figures",
            model,
            kind,
            seconds,
            0.0,
            effective_model=effective_model,
            note=note,
        )
    seconds = figure_count * REMOTE_SECONDS_PER_FIGURE
    usd, unknown = _image_stage_price(
        model, dims, figure_count, OUTPUT_TOKENS_PER_FIGURE
    )
    return _stage(
        "figures",
        model,
        kind,
        seconds,
        usd,
        unknown,
        effective_model=effective_model,
        note=note,
    )


def _review_stage(model: str | None, scanned_pages: int) -> EstimateStage:
    if not model:
        return _stage("review", None, "none", 0.0, 0.0)
    kind = _stage_kind(model)
    if scanned_pages <= 0 or kind == "cpu":  # the reviewer never runs on Tesseract
        return _stage("review", model, kind, 0.0, 0.0)
    if kind == "gpu":
        seconds = scanned_pages * LOCAL_SECONDS_PER_REVIEW_PAGE
        return _stage("review", model, kind, seconds, 0.0)
    seconds = scanned_pages * REMOTE_SECONDS_PER_PAGE_REVIEW
    tokens_per_page = REVIEW_ASSUMED_CHARS_PER_PAGE // 4
    review_tokens = tokens_per_page * scanned_pages
    usd, unknown = _cloud_price(model, review_tokens, review_tokens)
    return _stage("review", model, kind, seconds, usd, unknown)


def _extract_stage(pages: int) -> EstimateStage:
    return _stage("extract", None, "cpu", pages * EXTRACT_SECONDS_PER_PAGE, 0.0)


def _write_stage(pages: int) -> EstimateStage:
    return _stage("write", None, "cpu", pages * WRITE_SECONDS_PER_PAGE, 0.0)


def _effective_figure_model(
    ocr_model: str, figure_model: str | None, review_model: str | None = None
) -> str | None:
    """Mirrors `agents.image_ocr.effective_figure_model`, on explicit models
    rather than the global `llm_config` — same shared arithmetic
    (`gpu_memory.effective_figure_model`), so this estimate and the engine's
    own figure-model choice can never disagree about what "fits" means.

    Only collapses onto the OCR model when the two are local, genuinely
    distinct, and their combined `resident_gb` (together with any local,
    distinct `review_model` — see M9 in `gpu_memory.effective_figure_model`'s
    docstring) exceeds `gpu_limit_gb()` — not a blanket "both are local"
    rule. `_gpu_state` below still handles a pipeline whose figure model
    does *not* collapse (it fits fine, so it stays a second distinct local
    model in the resident-memory sum, right alongside a local
    `review_model`).
    """
    resolved, _message = gpu_memory.effective_figure_model(
        ocr_model, figure_model, review_model
    )
    return resolved


# ---------------------------------------------------------------------------
# Resident-memory arithmetic — replaces a blanket "two local models are
# blocked" rule with the actual question: do this pipeline's distinct local
# models, loaded at once, fit in what macOS leaves the GPU? The arithmetic
# itself (`RESIDENT_FACTOR`, `resident_gb`, `distinct_local_models`,
# `gpu_limit_gb`) now lives in `twomarkdown.batch.gpu_memory`, shared with
# `agents.image_ocr.effective_figure_model()` — imported above.
# ---------------------------------------------------------------------------

# Below this much spare headroom, whatever runs next on the GPU (Ollama's own
# context growing mid-run, a browser tab, a second job) risks tipping a
# pipeline this module says "fits" into the exact OOM it exists to catch.
_WARNING_HEADROOM_GB = 3.0


# N14: the sentinel `_format_gb_pair` returns for both strings once two
# genuinely different values still print identically at the maximum
# precision this module bothers with (e.g. 38.61 vs 38.60, both "38,6" at
# one decimal *and* both "38,61"/"38,60" only differ past what an operator
# can act on) — a caller checks for this pair and swaps in an "at the
# limit" phrasing instead of a bare number-vs-number comparison.
GB_TIE_SENTINEL = "≈ el límite"


def _format_gb_pair(a: float, b: float) -> tuple[str, str]:
    """Spanish-formatted `(a, b)` GB strings for a "X GB > Y GB" (or "X de Y
    GB") comparison line — whole numbers by default ("39 GB"), matching how
    an operator actually thinks about GPU memory.

    Rounding both to the nearest whole GB can make two genuinely different
    values collide (e.g. 39.3 and 38.6 both round to 39 — see N1), which
    would print the nonsensical "39 GB > 39 GB" for a comparison that only
    ever appears because the two values differ. When that happens, one
    decimal is kept instead for *both* values, comma as the decimal
    separator (Spanish locale) — "39,3" and "38,6".

    N14: one decimal can still collide (38.61 vs 38.60 both round to "38,6")
    — escalate to two decimals before giving up. Two decimals is also the
    cap this module ever prints: past that, the two values are equal for
    every purpose an operator has, so a pair still tied there returns
    `(GB_TIE_SENTINEL, GB_TIE_SENTINEL)` instead of ever-longer digits.
    """
    for decimals in (0, 1, 2):
        a_str = f"{a:.{decimals}f}"
        b_str = f"{b:.{decimals}f}"
        if a_str != b_str:
            return a_str.replace(".", ","), b_str.replace(".", ",")
    return GB_TIE_SENTINEL, GB_TIE_SENTINEL


def _join_stage_names(names: list[str]) -> str:
    """ "OCR", "OCR y figuras", or "OCR, figuras y revisión" — Spanish-style
    comma list with a final "y", never a trailing "and" left dangling."""
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + f" y {names[-1]}"


def _blocked_body(
    residents: list[tuple[str, float]],
    total: float,
    limit: float,
    stage_labels: list[str],
) -> str:
    total_str, limit_str = _format_gb_pair(total, limit)
    parts = " + ".join(f"{name} ≈ {round(gb)} GB" for name, gb in residents)
    # C14: with exactly two local models "el segundo" (the second one loaded)
    # is a concrete, correct claim about who runs out of memory first; with
    # three or more it stops being true of any one model in particular, so
    # the wording has to generalize instead of pretending there's still a
    # "second" — same reasoning behind the count-aware title below.
    loaded_clause = (
        "Con ambos cargados el segundo se queda sin memoria"
        if len(residents) == 2
        else "Con todos cargados, alguno se queda sin memoria"
    )
    # N13: "Usa el mismo modelo en ambas etapas" hardcoded a two-stage
    # pipeline even when the body above lists three distinct models (OCR,
    # figures, and a local review model) — naming which stages are actually
    # in play instead of assuming there are exactly two of them.
    stages_clause = _join_stage_names(stage_labels)
    # N14: `total_str`/`limit_str` collapse to the same GB_TIE_SENTINEL when
    # they still print identically at two decimals — "X GB residentes > Y GB"
    # would then read as "≈ el límite GB residentes > ≈ el límite GB",
    # nonsensical for the same reason the plain rounding collision was.
    if total_str == GB_TIE_SENTINEL:
        memory_clause = f"{parts} {GB_TIE_SENTINEL} que macOS deja a la GPU."
    else:
        memory_clause = (
            f"{parts} = {total_str} GB residentes > {limit_str} GB que macOS "
            "deja a la GPU."
        )
    return (
        f"{memory_clause} {loaded_clause} al codificar la imagen y las llamadas "
        f"siguientes fallan. Usa el mismo modelo en {stages_clause}, uno más "
        "pequeño, o pasa una etapa a la nube."
    )


def _gpu_state(
    ocr_model: str, figure_model: str | None, review_model: str | None
) -> tuple[BlockedInfo | None, WarningInfo | None, float, float, float]:
    """Resident-memory arithmetic for one pipeline's local models, computed
    once: `(blocked, warning, gpu_resident_gb, gpu_limit_gb, gpu_headroom_gb)`.

    `figure_model` has already gone through `_effective_figure_model`, which
    only collapses it onto `ocr_model` when that pair would not otherwise
    fit — so this can genuinely see up to three distinct local models (page,
    figures, and a local `review_model` that names something else; a
    reviewer never shares GPU residency with OCR/figures the way those two
    can). `blocked` fires only when there are at least two distinct local
    models and their combined resident size exceeds `gpu_limit_gb`; a single
    local model (however large) or every model naming the same tag is never
    blocked, however little headroom is left — `warning` covers that case
    instead.
    """
    models = gpu_memory.distinct_local_models(ocr_model, figure_model, review_model)
    limit = gpu_memory.gpu_limit_gb()
    if not models:
        return None, None, 0.0, limit, round(limit, 2)

    sizes = gpu_memory.ollama_installed_sizes()
    residents = [(gpu_memory.ollama_tag(m), resident_gb(m, sizes)) for m in models]
    total = round(sum(gb for _, gb in residents), 2)
    headroom = round(limit - total, 2)

    if len(models) > 1 and limit > 0 and total > limit:
        # C14: the title used to be hardcoded to "Dos modelos…" regardless of
        # how many distinct local models were actually enumerated — with
        # OCR/figures/review all pointing at different local models that's
        # three, and the banner's own body (below) correctly listed all
        # three while the title still said "two". Match the title to
        # `len(models)` instead of assuming the two-model case is the only
        # one that blocks.
        title = (
            "Dos modelos locales no caben en la GPU a la vez"
            if len(models) == 2
            else "Los modelos locales no caben en la GPU a la vez"
        )
        # Which named stages contributed a still-distinct resident model —
        # a figure model already collapsed onto OCR (see
        # `_effective_figure_model`) shares OCR's own tag by then, so it must
        # not also be named "figuras" here: walk the stages in their
        # OCR -> figures -> review order and keep only the first stage to
        # introduce each distinct tag, mirroring `distinct_local_models`'s
        # own first-occurrence dedup.
        stage_labels: list[str] = []
        seen_tags: set[str] = set()
        for label, m in (
            ("OCR", ocr_model),
            ("figuras", figure_model),
            ("revisión", review_model),
        ):
            if not m or not is_local_model(m):
                continue
            tag = gpu_memory.ollama_tag(m)
            if tag in seen_tags:
                continue
            seen_tags.add(tag)
            stage_labels.append(label)
        blocked = BlockedInfo(
            title=title,
            body=_blocked_body(residents, total, limit, stage_labels),
        )
        return blocked, None, total, limit, headroom

    if limit > 0 and headroom < _WARNING_HEADROOM_GB:
        total_str, limit_str = _format_gb_pair(total, limit)
        # N14: same tie sentinel as `_blocked_body` — "X de Y GB" is
        # meaningless once X and Y print identically even at two decimals.
        fits_clause = (
            GB_TIE_SENTINEL
            if total_str == GB_TIE_SENTINEL
            else f"{total_str} de {limit_str} GB"
        )
        warning = WarningInfo(
            title="Memoria GPU ajustada",
            body=(f"Cabe justo: {fits_clause}. Cierra otras apps que usen la GPU."),
        )
        return None, warning, total, limit, headroom

    return None, None, total, limit, headroom


def _blocked_info(
    ocr_model: str, figure_model: str | None, review_model: str | None
) -> BlockedInfo | None:
    """Convenience wrapper over `_gpu_state` for a caller that only needs the
    yes/no + message — `server/app.py`'s `_reject_if_blocked`, which checks
    this independently of `/api/estimate` before `POST /api/jobs` runs
    anything."""
    blocked, _warning, _total, _limit, _headroom = _gpu_state(
        ocr_model, figure_model, review_model
    )
    return blocked


_ALTERNATIVE_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    # (label, patched field, patched model)
    ("figures", "figure_model", "openai:gpt-4o-mini"),
    ("ocr", "ocr_model", "openai:gpt-4o"),
    ("ocr", "ocr_model", "tesseract"),
)

_ALTERNATIVE_LABELS = {
    ("figures", "openai:gpt-4o-mini"): "Describir figuras con gpt-4o-mini",
    ("ocr", "openai:gpt-4o"): "Leer también en la nube (gpt-4o)",
    ("ocr", "tesseract"): "Tesseract en vez del modelo de OCR",
}


def _alternatives(
    inspect_result: InspectResponse, pipeline: Pipeline
) -> list[EstimateAlternative]:
    """The three swaps the mockup's Pipeline simulator always offers, each
    priced by recursing into `estimate()` with that one field patched.

    `_for_alternative=True` on the recursive call is the base case: an
    alternative's own estimate never grows alternatives of its own, or this
    would never terminate.
    """
    alternatives: list[EstimateAlternative] = []
    for stage, field, model in _ALTERNATIVE_CANDIDATES:
        if stage == "figures" and not pipeline.describe_figures:
            continue
        current = getattr(pipeline, field) or ""
        if normalize_model_id(current) == normalize_model_id(model):
            continue  # already this alternative — nothing to offer
        patch = {field: model}
        patched = pipeline.model_copy(update=patch)
        result = estimate(inspect_result, patched, _for_alternative=True)
        alternatives.append(
            EstimateAlternative(
                label=_ALTERNATIVE_LABELS[(stage, model)],
                pipeline_patch=patch,
                total_seconds=result.total_seconds,
                total_usd=result.total_usd,
            )
        )
    return alternatives


def estimate(
    inspect_result: InspectResponse,
    pipeline: Pipeline,
    *,
    _for_alternative: bool = False,
) -> EstimateResponse:
    """Time and cost for converting `inspect_result` with `pipeline`.

    Scheduling mirrors the desktop-app mockup's simulator and the engine it
    encodes: every local stage shares one GPU permit (so their seconds sum in
    series, not in parallel); cloud stages share up to 4 permits; CPU stages
    scale with `pipeline.workers`. `_for_alternative` is an internal recursion
    guard — see `_alternatives`.
    """
    totals = inspect_result.totals
    ocr_model = pipeline.ocr_model
    review_model = pipeline.review_model or None
    figure_model, figure_note = (
        gpu_memory.effective_figure_model(
            ocr_model, pipeline.figure_model, review_model
        )
        if pipeline.describe_figures
        else (None, None)
    )
    figure_count = totals.figures if pipeline.describe_figures else 0

    ocr_dims = _ocr_render_dimensions(inspect_result.files)
    figure_dims = _figure_render_dimensions(inspect_result.files)

    stages = [
        _extract_stage(totals.pages),
        _ocr_stage(ocr_model, totals.scanned_pages, ocr_dims),
        _figures_stage(
            figure_model,
            figure_count,
            figure_dims,
            enabled=pipeline.describe_figures,
            note=figure_note,
        ),
        _review_stage(review_model, totals.scanned_pages),
        _write_stage(totals.pages),
    ]

    gpu_seconds = sum(s.seconds for s in stages if s.kind == "gpu")
    cloud_seconds = sum(s.seconds for s in stages if s.kind == "cloud")
    cpu_seconds = sum(s.seconds for s in stages if s.kind == "cpu")

    workers = max(1, pipeline.workers)
    parallel_files = 1 if gpu_seconds > 0 else workers  # effective_workers()
    cloud_parallel = min(4, workers)  # _remote_lock

    t_gpu = gpu_seconds
    t_cloud = cloud_seconds / cloud_parallel if cloud_parallel else cloud_seconds
    t_cpu = cpu_seconds / workers
    total_seconds = max(t_gpu, t_cloud, t_cpu)

    bottleneck: Bottleneck
    if total_seconds == t_gpu and gpu_seconds > 0:
        bottleneck = "gpu"
    elif total_seconds == t_cloud and cloud_seconds > 0:
        bottleneck = "cloud"
    else:
        bottleneck = "cpu"

    unknown_any = any(s.unknown_price for s in stages)
    total_usd = None if unknown_any else sum((s.usd or 0.0) for s in stages)

    blocked, warning, gpu_resident, gpu_limit, gpu_headroom = _gpu_state(
        ocr_model, figure_model, review_model
    )

    return EstimateResponse(
        stages=stages,
        total_seconds=total_seconds,
        total_usd=total_usd,
        parallel_files=parallel_files,
        bottleneck=bottleneck,
        blocked=blocked,
        warning=warning,
        alternatives=(
            [] if _for_alternative else _alternatives(inspect_result, pipeline)
        ),
        gpu_resident_gb=gpu_resident,
        gpu_limit_gb=gpu_limit,
        gpu_headroom_gb=gpu_headroom,
    )


_ZERO_TOTALS = InspectTotals(files=0, pages=0, scanned_pages=0, figures=0, bytes=0)


def estimate_pipeline_only(pipeline: Pipeline) -> EstimateResponse:
    """`POST /api/estimate` with no `inspect_id` (N1): the GPU verdict for
    `pipeline` alone, with no file data to size the per-stage seconds/usd
    from.

    `_gpu_state` (called by `estimate()` below) only ever looks at the
    pipeline's model ids — never at `inspect_result` — so the
    `blocked`/`warning`/`gpu_resident_gb`/`gpu_limit_gb`/`gpu_headroom_gb`
    fields it produces are exactly the same real engine verdict whether or
    not a folder has been inspected yet. Reusing `estimate()` against a
    zero-totals, zero-files `InspectResponse` gets that verdict, plus
    correctly-shaped (kind-aware, all-zero/null) stages, for free — every
    stage builder already returns `seconds=0.0`/`usd=None` on a zero count,
    so there is no separate "pipeline-only" stage-building code path to keep
    in sync with the real one.

    This is exactly the guard the Pipeline editor needs (N1/C15): it can
    always ask the engine for the GPU verdict of the pipeline being edited,
    without first inspecting a folder and without approximating GPU
    residency from a client-side model-size table that can disagree with
    the engine's real, installed-model sizes.
    """
    fake_inspection = InspectResponse(
        inspect_id="",
        root="",
        files=[],
        totals=_ZERO_TOTALS,
        default_output="",
    )
    result = estimate(fake_inspection, pipeline)
    return result.model_copy(update={"estimate_scope": "pipeline_only"})
