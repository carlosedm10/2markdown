import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_INCLUDE_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".rst",
        ".csv",
        ".json",
        ".xml",
        ".epub",
        ".mobi",
        ".azw",
        ".azw3",
        ".fb2",
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
        ".wav",
        ".mp3",
        ".zip",
        ".xmind",
        ".m",
        ".mlx",
        ".msg",
        ".eml",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".doc",
        ".ppt",
        ".pages",
        ".key",
        ".numbers",
    }
)

IWORK_BUNDLE_SUFFIXES = frozenset({".pages", ".key", ".numbers"})

SKIP_DIR_NAMES = frozenset({".git", "__pycache__", ".venv", "node_modules"})

# Fallbacks when .ocr-mode is absent. `make build` writes .ocr-mode, not this file.
OCR_BACKEND: Literal["tesseract", "ollama"] = "tesseract"
LLM_ENABLED = False
OLLAMA_VISION_MODEL = "ollama:qwen2.5vl:32b"


def _load_ocr_mode_file() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / ".ocr-mode"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


_ocr_mode = _load_ocr_mode_file()
if _ocr_mode.get("backend") in {"tesseract", "ollama"}:
    OCR_BACKEND = _ocr_mode["backend"]
    LLM_ENABLED = OCR_BACKEND == "ollama" or bool(_ocr_mode.get("llm_enabled"))
if isinstance(_ocr_mode.get("vision_model"), str) and _ocr_mode["vision_model"]:
    OLLAMA_VISION_MODEL = _ocr_mode["vision_model"]


def ollama_host() -> str:
    """The hostname to reach the operator's Ollama at.

    Two very different processes import this config. Inside the backend
    Docker container (`make up`/`make process`, see `compose.yaml`'s
    `extra_hosts`) the host's Ollama is reached through the compose-provided
    `host.docker.internal` alias — `docs/README.md`'s "Host GPU, container
    CPU". The desktop server (`make app`/`make app-web`) runs uvicorn
    natively on the host instead (it needs the host GPU, host Ollama, and
    arbitrary user folders no bind mount can offer — see docs/README.md's Key
    decisions), where that name does not resolve and Ollama listens on
    localhost. `/.dockerenv` is the standard marker Docker leaves in every
    container's root, checked once here rather than per-request since a
    process does not change container status while it runs.
    """
    return "host.docker.internal" if Path("/.dockerenv").is_file() else "localhost"


_OLLAMA_HOST = ollama_host()


class ConversionConfig(BaseModel):
    """Batch and OCR defaults. Edit here; OCR engine also reads `.ocr-mode`."""

    input_dir: Path = Path(".")
    output_dir: Path = Path(".")
    skip_existing: bool = True
    convert_existing_md: bool = False
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS
    parallel_workers: int = 4
    # Vision OCR runs tens of seconds per page, so the Tesseract-era budget would
    # silently truncate a long scanned document mid-file.
    # A file is not slow for being long, it is slow for having many pages. The
    # budget is therefore per unit of work: each page or figure that needs the
    # model earns its own allowance, and this is only the floor for small files.
    # A genuine hang is caught earlier and more precisely by the per-request HTTP
    # timeout (llm_config.request_timeout_sec), which bounds one call.
    file_timeout_sec: float | None = 600.0 if LLM_ENABLED else 300.0
    timeout_safety_factor: float = 3.0
    explode_zip: bool = True
    sniff_filetype: bool = True

    ocr_enabled: bool = True
    ocr_backend: Literal["tesseract", "ollama"] = OCR_BACKEND
    tesseract_lang: str = "eng+spa"
    ocr_hybrid: bool = True
    ocr_confidence_min: float = 60.0
    fetch_remote_images: bool = False
    min_image_px: int = 64

    clean_markdown: bool = True
    extract_tables: bool = True
    describe_figures: bool = True
    extract_assets: bool = True
    emit_chunks: bool = False
    chunk_max_chars: int = 1500
    chunk_overlap: int = 100
    write_export_report: bool = True
    project_telemetry_dir: Path | None = None


class PdfOcrConfig(BaseModel):
    pdf_ocr_enabled: bool = True
    pdf_ocr_min_chars: int = 50
    # 300, not 200: a page is rendered at this DPI and then fitted to
    # llm_ocr_max_dimension, so the two together decide whether a prime mark or
    # the difference between a handwritten f and g survives. Measured on one page
    # of 17 handwritten integration rules: at 1568px the model misread 3 symbols
    # and wrote 2 mathematically false rules; at 2200px it misread none and wrote
    # none. Cost is unchanged in wall clock (128s a page against 87-160s before).
    pdf_ocr_dpi: int = 300
    pdf_ocr_max_pages: int | None = None
    # Tesseract returns confident nonsense on handwriting, which used to lock the
    # vision model out entirely. Below this mean word confidence, re-OCR with the
    # vision model instead of trusting the result.
    pdf_ocr_llm_min_confidence: float = 75.0
    # PowerPoint equation objects extract as scrambled token soup with plenty of
    # characters, so the "too few chars" test never fires. Measured on this corpus:
    # readable pages peak at 54% single-character tokens, scrambled ones run 65-86%.
    pdf_text_scramble_ratio: float = 0.6
    pdf_text_scramble_min_tokens: int = 25
    # Some renderers position Keynote text per character, so the extractor reads
    # "Eval uaci ón". Spurious gaps form a second, narrower population; above this
    # wide/narrow ratio the page is treated as fragmented and rejoined.
    # Two independent guards, both required. Neither is safe alone: healthy pages
    # reach 84% short tokens while fragmented ones start at 85%, and the gap
    # populations can sit as close as 1.35 apart. Together they leave no overlap.
    pdf_fragment_short_tokens: float = 0.80
    pdf_fragment_gap_ratio: float = 1.30
    pdf_fragment_min_words: int = 12
    # Only ever join two short pieces. A mid-word split leaves fragments
    # ("Eval", "uaci", "ón"); a real word like "Operaciones" is never a fragment,
    # so this stops dense maths pages collapsing into "Operacionesconmatrices".
    pdf_fragment_max_piece: int = 5


class FigureConfig(BaseModel):
    """Detection and rendering of figure regions (vector plots, circuits, diagrams)."""

    figures_enabled: bool = True
    figure_dpi: int = 300
    figure_min_pt: float = 60.0
    figure_padding_pt: float = 6.0
    figure_merge_gap_pt: float = 12.0
    # A plot exports as few paths (frame, gridlines, curve): 3 is common. Size and
    # the hairline filter already reject rules and bullets, so keep this low.
    figure_min_vector_parts: int = 2
    figure_max_area_ratio: float = 0.85
    figure_max_text_ratio: float = 0.45
    # A figure carries short labels; a Beamer theorem box carries sentences. Above
    # this many characters the region is prose in a coloured box, not a diagram.
    figure_max_text_chars: int = 300
    # A text block only counts as prose above this length. Axis ticks, units and
    # node labels are short; counting them rejects the plots we most want to keep.
    figure_prose_block_min_chars: int = 40
    # How far outside a drawing to pull in its labels. Axis ticks sit just beyond
    # the vector bounding box; without them the model invents axes and units.
    figure_label_margin_pt: float = 24.0
    # Strongest signal measured on this corpus: a theme's block boxes are full-width
    # bands (31-39% of their primitives span the page), while plots, circuits and
    # block diagrams are built from many narrow strokes (0-15%).
    # Slide-theme chrome (title banners, decorative blocks) is 100% filled shapes
    # with no strokes; every real figure measured here has strokes (<=73% fills).
    figure_max_fill_ratio: float = 0.95
    figure_wide_part_ratio: float = 0.8
    figure_max_wide_parts: float = 0.3
    figure_max_per_page: int = 6
    # On by default. Captioning dominates runtime (916s of a measured 1092s run)
    # and the caption is advisory — the inlined crop is the artefact to trust — so
    # turn it off with --no-describe-figures when throughput matters.
    describe_figures_llm: bool = True


class LLMConfig(BaseModel):
    """Model selection is provider-neutral: "<provider>:<model>".

    pydantic-ai resolves the provider from the prefix, so the same setting takes
    "ollama:qwen2.5vl:32b", "openai:gpt-5.2" or "anthropic:claude-sonnet-4-5".
    A bare name is assumed to be Ollama, which keeps older `.ocr-mode` files
    working. API keys come from the environment (see .env_template); only
    Ollama needs a base URL, because it is the one provider we host ourselves.
    """

    llm_enabled: bool = LLM_ENABLED
    ollama_base_url: str = f"http://{_OLLAMA_HOST}:11434/v1"
    vision_model: str = OLLAMA_VISION_MODEL
    # Figure descriptions run on their own, smaller model: it matches model size
    # to stakes (a wrong transcription is permanent, a caption sits beside its
    # crop). Two local models do not fit one GPU, so when both are local the page
    # model is reused — see effective_figure_model().
    figure_model: str = "ollama:qwen2.5vl:7b"
    # A second pass that proofreads each page's transcription from the text
    # alone: a symbol one letter off from the one used on every other line, a
    # Greek letter written as its Latin lookalike, an unclosed bracket. It never
    # sees the page, which is what keeps it safe — it cannot know what is missing
    # and so has no grounds to add anything. A small hosted model suits it: the
    # edits are tiny, a different model fails in different places, and it
    # competes for no local VRAM. Empty disables the pass.
    review_model: str = ""
    # A correction pass that rewrites is worse than none. The reviewer never sees
    # the page, so it may only fix characters the text itself gives away; below
    # this character-level similarity it reworded rather than proofread, and the
    # transcription is kept. Fixing a symbol moves the ratio by a fraction of a
    # percent, so this leaves ample room for real corrections.
    review_min_similarity: float = 0.97
    # Vision models bill by tiles of pixel dimensions, not file size, so this is
    # the only setting that changes token cost. Dense handwriting needs every
    # pixel: on one page of 17 integration rules, 1568 lost three symbols to
    # lookalikes and produced two false rules, while 2200 produced neither, at
    # the same wall clock. Below 1024 a transcribed formula degrades badly.
    llm_ocr_max_dimension: int = 2200
    # Figure crops are diagrams, not dense prose, and their captions are advisory.
    # They survive a smaller raster, which is ~30% fewer tokens per caption.
    llm_figure_max_dimension: int = 1024
    llm_ocr_max_bytes: int = 1_500_000
    llm_ocr_jpeg_quality: int = 85
    # Without this the HTTP client waits forever. A dropped host.docker.internal
    # connection then strands the worker holding the one vision permit, and every
    # other worker blocks behind it: the whole batch deadlocks, not just one file.
    connect_timeout_sec: float = 15.0
    request_timeout_sec: float = 300.0
    # A local model serves one request at a time; a hosted API does not, and
    # serializing against it would waste most of the wall clock. Four, not
    # more: docs/desktop-app.md and the Pipeline mockup's "4 permisos" both
    # document this as the number of `_remote_lock` permits, so the default
    # here must agree with what the UI tells the user to expect.
    remote_max_concurrency: int = 4
    # Hosted providers answer 429 when a batch outruns the account's quota.
    rate_limit_max_retries: int = 5
    rate_limit_initial_delay_sec: float = 2.0
    # EXPERIMENTAL. `agents.image_ocr._page_ocr_lock`'s permit count — the
    # queue that serializes every local (Ollama) call, page OCR and figure
    # captioning alike, because one GPU normally answers one request at a
    # time (overlapping requests corrupt Ollama's reply instead of queueing
    # it — see that module's own docstring). Raising this past 1 asks Ollama
    # to actually run two local calls at once, which only makes sense on a
    # machine with GPU headroom to spare; it is not validated against
    # `gpu_memory.gpu_limit_gb()` the way `resident_gb` is, so it is the
    # operator's own judgment call, not one this app can verify. Bounded to
    # 1..2 (see `Settings.local_gpu_permits`) — untested and unmeasured past
    # 2, unlike every other constant in this file. Never changes
    # `effective_workers()`: permits let one file's own local calls
    # interleave, they do not make a batch convert more files at once.
    local_gpu_permits: int = 1


class MarkItDownConfig(BaseModel):
    markitdown_enable_plugins: bool = False


class IWorkConfig(BaseModel):
    iwork_enabled: bool = True


class Secrets(BaseSettings):
    """Credentials. Source of truth is `.env` / the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


conversion_config = ConversionConfig()
pdf_ocr_config = PdfOcrConfig()
figure_config = FigureConfig()
llm_config = LLMConfig()
markitdown_config = MarkItDownConfig()
iwork_config = IWorkConfig()
secrets = Secrets()
