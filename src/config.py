from pathlib import Path
from typing import Literal

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
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".wav",
        ".mp3",
        ".zip",
        ".msg",
        ".pages",
        ".key",
        ".numbers",
    }
)

IWORK_BUNDLE_SUFFIXES = frozenset({".pages", ".key", ".numbers"})

SKIP_DIR_NAMES = frozenset({".git", "__pycache__", ".venv", "node_modules"})


class ConversionConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    input_dir: Path = Path(".")
    output_dir: Path = Path(".")
    skip_existing: bool = True
    ocr_enabled: bool = True
    ocr_backend: Literal["tesseract", "ollama"] = "tesseract"
    fetch_remote_images: bool = False
    include_extensions: frozenset[str] = DEFAULT_INCLUDE_EXTENSIONS
    convert_existing_md: bool = True


class PdfOcrConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    pdf_ocr_enabled: bool = True
    pdf_ocr_min_chars: int = 50
    pdf_ocr_dpi: int = 200
    pdf_ocr_max_pages: int | None = None


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_enabled: bool = False
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_vision_model: str = "ollama:llama3.2-vision:11b"


class MarkItDownConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    markitdown_enable_plugins: bool = False


class IWorkConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    iwork_enabled: bool = True
    iwork_backend: Literal["native", "kreuzberg"] = "native"
    iwork_use_app_export: bool = False


conversion_config = ConversionConfig()
pdf_ocr_config = PdfOcrConfig()
llm_config = LLMConfig()
markitdown_config = MarkItDownConfig()
iwork_config = IWorkConfig()
