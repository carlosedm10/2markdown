"""OCR for markdown image references and raw image bytes."""

import logging
import re
import shutil
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytesseract
import requests
from PIL import Image, ImageOps

from src.config import conversion_config

logger = logging.getLogger(__name__)

RASTER_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

REMOTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def markdown_has_usable_text(markdown: str) -> bool:
    if not (markdown or "").strip():
        return False
    leftover = IMAGE_PATTERN.sub("", markdown)
    return bool(leftover.strip())


def is_raster_image(path: Path) -> bool:
    return path.suffix.lower() in RASTER_IMAGE_SUFFIXES


def is_tiny_image(image_bytes: bytes, min_px: int | None = None) -> bool:
    """Return True when both width and height are below min_px (logo/icon skip)."""
    if min_px is None:
        min_px = conversion_config.min_image_px
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            return width < min_px and height < min_px
    except Exception:
        return True


def _prepare_image_for_tesseract(image_bytes: bytes) -> tuple[Image.Image, Image.Image]:
    with Image.open(BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        grayscale = ImageOps.grayscale(img)
        return img, grayscale


def extract_text_with_tesseract(image_bytes: bytes) -> str:
    """Run pytesseract OCR on raw image bytes."""
    if shutil.which("tesseract") is None:
        logger.warning("tesseract binary not found on PATH")
        return ""

    try:
        img, grayscale = _prepare_image_for_tesseract(image_bytes)
        lang = conversion_config.tesseract_lang
        text = pytesseract.image_to_string(grayscale, lang=lang).strip()
        if not text:
            text = pytesseract.image_to_string(img, lang=lang).strip()
        return text
    except Exception as exc:
        logger.warning("Tesseract error: %s", exc)
        return ""


def tesseract_ocr_with_confidence(image_bytes: bytes) -> tuple[str, float]:
    """Run Tesseract OCR and return extracted text plus mean word confidence (0–100)."""
    if shutil.which("tesseract") is None:
        logger.warning("tesseract binary not found on PATH")
        return "", 0.0

    try:
        img, grayscale = _prepare_image_for_tesseract(image_bytes)
        lang = conversion_config.tesseract_lang
        text = pytesseract.image_to_string(grayscale, lang=lang).strip()
        if not text:
            text = pytesseract.image_to_string(img, lang=lang).strip()

        data = pytesseract.image_to_data(
            grayscale, lang=lang, output_type=pytesseract.Output.DICT
        )
        confidences = [int(conf) for conf in data["conf"] if int(conf) != -1]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return text, mean_conf
    except Exception as exc:
        logger.warning("Tesseract error: %s", exc)
        return "", 0.0


def describe_image_bytes(image_bytes: bytes) -> str:
    """Describe figure content via vision LLM when enabled."""
    if not conversion_config.describe_figures:
        return ""
    if is_tiny_image(image_bytes):
        return ""
    try:
        from src.config import llm_config

        if not llm_config.llm_enabled:
            return ""
        from src.agents.image_ocr import describe_image_bytes_llm

        return describe_image_bytes_llm(image_bytes).strip()
    except Exception as exc:
        logger.warning("Figure description failed: %s", exc)
        return ""


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
) -> str:
    if is_tiny_image(image_bytes):
        return ""

    if not conversion_config.ocr_hybrid:
        if ocr_fn is not None:
            return ocr_fn(image_bytes).strip()
        return extract_text_with_tesseract(image_bytes)

    text, confidence = tesseract_ocr_with_confidence(image_bytes)
    min_conf = conversion_config.ocr_confidence_min

    if confidence >= min_conf and text:
        return text

    if (
        ocr_fn is not None
        and ocr_fn is not extract_text_with_tesseract
    ):
        return ocr_fn(image_bytes).strip()

    return text


def _format_ocr_block(text: str) -> str:
    return f"\n### [OCR]\n\n{text}\n"


def _format_figure_block(description: str) -> str:
    return f"\n### [Figure]\n\n{description}\n"


def convert_image_file(
    path: Path,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
    existing_markdown: str = "",
) -> str:
    """OCR a standalone image when MarkItDown yields little or no text."""
    if markdown_has_usable_text(existing_markdown):
        return existing_markdown
    if not conversion_config.ocr_enabled:
        return existing_markdown

    ocr_text = ocr_image_bytes(path.read_bytes(), ocr_fn=ocr_fn)
    if not ocr_text:
        return existing_markdown

    return f"## {path.name} — OCR\n\n{ocr_text}"


def enrich_markdown_images(
    markdown_content: str,
    source_file: Path,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
) -> str:
    """
    Find markdown image references and append OCR-extracted text blocks.
    """

    def _replace(match: re.Match) -> str:
        ref = match.group(2).strip()
        image_bytes: bytes | None = None

        if ref.startswith(("http://", "https://", "//")):
            image_bytes = _fetch_remote_image(ref)
        else:
            resolved = _resolve_image_path(ref, source_file)
            if resolved is not None:
                image_bytes = resolved.read_bytes()

        if not image_bytes:
            return match.group(0)

        ocr_text = ocr_image_bytes(image_bytes, ocr_fn=ocr_fn)
        if ocr_text:
            return match.group(0) + _format_ocr_block(ocr_text)

        if conversion_config.describe_figures and not is_tiny_image(image_bytes):
            description = describe_image_bytes(image_bytes)
            if description:
                return match.group(0) + _format_figure_block(description)

        return match.group(0)

    return IMAGE_PATTERN.sub(_replace, markdown_content)


def _resolve_image_path(ref: str, source_file: Path) -> Path | None:
    ref = ref.strip().split()[0]  # drop optional title fragment
    if ref.startswith(("http://", "https://", "//")):
        return None
    path = Path(ref)
    if path.is_absolute() and path.is_file():
        return path
    candidate = (source_file.parent / ref).resolve()
    if candidate.is_file():
        return candidate
    return None


def _fetch_remote_image(url: str) -> bytes | None:
    if not conversion_config.fetch_remote_images:
        return None
    try:
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            return None
        resp = requests.get(url, timeout=20, stream=True)
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("image/"):
            return None
        content_length = resp.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > REMOTE_IMAGE_MAX_BYTES:
                    return None
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > REMOTE_IMAGE_MAX_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception as exc:
        logger.warning("Failed to fetch image %s: %s", url, exc)
        return None
