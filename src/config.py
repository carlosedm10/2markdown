from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_INCLUDE_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
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
        ".wav",
        ".mp3",
        ".zip",
        ".msg",
        ".eml",
        ".doc",
        ".ppt",
        ".pages",
        ".key",
        ".numbers",
    }
)

IWORK_BUNDLE_SUFFIXES = frozenset({".pages", ".key", ".numbers"})

SKIP_DIR_NAMES = frozenset({".git", "__pycache__", ".venv", "node_modules"})

# --- OCR mode (rewritten by scripts/set_ocr_mode.py via make build) ---
OCR_BACKEND: Literal["tesseract", "ollama"] = "tesseract"
LLM_ENABLED = False
OLLAMA_VISION_MODEL = "ollama:moondream"
# --- end OCR mode ---


class ConversionConfig(BaseModel):
    """Batch and OCR defaults. Edit here; not read from `.env`."""

    input_dir: Path = Path(".")
    output_dir: Path = Path(".")
    skip_existing: bool = True
    convert_existing_md: bool = False
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS
    parallel_workers: int = 1
    file_timeout_sec: float | None = 300.0
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


class PdfOcrConfig(BaseModel):
    pdf_ocr_enabled: bool = True
    pdf_ocr_min_chars: int = 50
    pdf_ocr_dpi: int = 200
    pdf_ocr_max_pages: int | None = None


class LLMConfig(BaseModel):
    llm_enabled: bool = LLM_ENABLED
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_vision_model: str = OLLAMA_VISION_MODEL
    llm_ocr_max_dimension: int = 1568
    llm_ocr_max_bytes: int = 1_500_000
    llm_ocr_jpeg_quality: int = 85


class MarkItDownConfig(BaseModel):
    markitdown_enable_plugins: bool = False


class IWorkConfig(BaseModel):
    iwork_enabled: bool = True
    iwork_backend: Literal["native", "kreuzberg"] = "native"
    iwork_use_app_export: bool = False


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
llm_config = LLMConfig()
markitdown_config = MarkItDownConfig()
iwork_config = IWorkConfig()
secrets = Secrets()
