"""Scanned PDF fallback: rasterize pages and OCR with Tesseract or Ollama."""

import logging
from collections.abc import Callable
from pathlib import Path

import fitz

from src.config import pdf_ocr_config

logger = logging.getLogger(__name__)


def should_fallback(markdown: str, *, suffix: str = "") -> bool:
    if not pdf_ocr_config.pdf_ocr_enabled:
        return False
    if suffix.lower() != ".pdf":
        return False
    text = (markdown or "").strip()
    return len(text) < pdf_ocr_config.pdf_ocr_min_chars


def _render_page_pixmap(doc: fitz.Document, page_index: int) -> bytes:
    page = doc[page_index]
    zoom = pdf_ocr_config.pdf_ocr_dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return pix.tobytes("png")


def extract_pages(
    pdf_path: Path,
    *,
    ocr_fn: Callable[[bytes], str],
) -> list[tuple[int, str]]:
    """OCR each PDF page; returns list of (page_number, text)."""
    results: list[tuple[int, str]] = []
    max_pages = pdf_ocr_config.pdf_ocr_max_pages

    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        limit = page_count if max_pages is None else min(page_count, max_pages)

        for i in range(limit):
            page_num = i + 1
            logger.info(
                "PDF page OCR %s/%s: %s",
                page_num,
                limit,
                pdf_path.name,
            )
            png_bytes = _render_page_pixmap(doc, i)
            text = ocr_fn(png_bytes).strip()
            if text:
                results.append((page_num, text))

    return results


def _format_page_sections(pages: list[tuple[int, str]]) -> str:
    blocks: list[str] = []
    for page_num, text in pages:
        blocks.append(f"## Page {page_num} — OCR\n\n```\n{text}\n```")
    return "\n\n".join(blocks)


def merge(markdown: str, pages: list[tuple[int, str]]) -> str:
    if not pages:
        return markdown
    ocr_section = _format_page_sections(pages)
    base = (markdown or "").strip()
    if base:
        return f"{base}\n\n## Scanned pages (OCR fallback)\n\n{ocr_section}"
    return ocr_section
