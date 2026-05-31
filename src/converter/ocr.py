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

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def extract_text_with_tesseract(image_bytes: bytes) -> str:
    """Run pytesseract OCR on raw image bytes."""
    if shutil.which("tesseract") is None:
        logger.warning("tesseract binary not found on PATH")
        return ""

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("L", "RGB"):
                img = img.convert("RGB")

            grayscale = ImageOps.grayscale(img)
            text = pytesseract.image_to_string(grayscale).strip()
            if not text:
                text = pytesseract.image_to_string(img).strip()
            return text
    except Exception as exc:
        logger.warning("Tesseract error: %s", exc)
        return ""


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
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("image/"):
            return None
        return resp.content
    except Exception as exc:
        logger.warning("Failed to fetch image %s: %s", url, exc)
        return None


def ocr_image_bytes(
    image_bytes: bytes,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
) -> str:
    if ocr_fn is not None:
        return ocr_fn(image_bytes).strip()
    return extract_text_with_tesseract(image_bytes)


def _format_ocr_block(text: str) -> str:
    return f"\n### [OCR generated text]\n\n```\n{text}\n```\n"


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
        return match.group(0)

    return IMAGE_PATTERN.sub(_replace, markdown_content)
