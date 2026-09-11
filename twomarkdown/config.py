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
OLLAMA_VISION_MODEL = "ollama:moondream"


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
    file_timeout_sec: float | None = 1800.0 if LLM_ENABLED else 300.0
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
    pdf_ocr_dpi: int = 200
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
    # Descriptions need a vision model; without one, figures are still rendered
    # and linked inline, just not described.
    describe_figures_llm: bool = True


class LLMConfig(BaseModel):
    llm_enabled: bool = LLM_ENABLED
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_vision_model: str = OLLAMA_VISION_MODEL
    llm_ocr_max_dimension: int = 1568
    llm_ocr_max_bytes: int = 1_500_000
    llm_ocr_jpeg_quality: int = 85
    # Without this the HTTP client waits forever. A dropped host.docker.internal
    # connection then strands the worker holding the one vision permit, and every
    # other worker blocks behind it: the whole batch deadlocks, not just one file.
    ollama_connect_timeout_sec: float = 15.0
    ollama_request_timeout_sec: float = 300.0


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
