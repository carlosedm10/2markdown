"""Scanned PDF fallback: rasterize pages and OCR with Tesseract or Ollama."""

import logging
from collections.abc import Callable
from pathlib import Path

import fitz
from tqdm import tqdm

from src.config import pdf_ocr_config

logger = logging.getLogger(__name__)


def should_fallback(
    markdown: str,
    *,
    suffix: str = "",
    pdf_path: Path | None = None,
) -> bool:
    if not pdf_ocr_config.pdf_ocr_enabled:
        return False
    if suffix.lower() != ".pdf":
        return False
    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    if pdf_path is not None:
        with fitz.open(pdf_path) as doc:
            for page in doc:
                if len(page.get_text().strip()) < min_chars:
                    return True
        return False
    text = (markdown or "").strip()
    return len(text) < min_chars


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
    show_progress: bool = False,
) -> list[tuple[int, str]]:
    """OCR PDF pages with insufficient native text; returns (page_number, text)."""
    results: list[tuple[int, str]] = []
    max_pages = pdf_ocr_config.pdf_ocr_max_pages
    min_chars = pdf_ocr_config.pdf_ocr_min_chars

    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        limit = page_count if max_pages is None else min(page_count, max_pages)

        page_indices = range(limit)
        if show_progress:
            page_indices = tqdm(
                page_indices,
                desc=f"PDF OCR {pdf_path.name}",
                unit="page",
                leave=False,
            )

        for i in page_indices:
            page = doc[i]
            if len(page.get_text().strip()) >= min_chars:
                continue

            page_num = i + 1
            if not show_progress:
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


def _page_native_text(page: fitz.Page) -> str:
    try:
        blocks = page.get_text("blocks")
        if blocks:
            sorted_blocks = sorted(blocks, key=lambda block: (block[1], block[0]))
            parts = [block[4].strip() for block in sorted_blocks if block[4].strip()]
            if parts:
                return "\n".join(parts)
    except Exception:
        logger.debug("Block-based text extraction failed; using default get_text()")
    return page.get_text().strip()


def _group_by_page(items: list[tuple[int, str]]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for page_num, content in items:
        grouped.setdefault(page_num, []).append(content)
    return grouped


def _should_append_markitdown(markitdown_text: str, native_concat: str) -> bool:
    markitdown = (markitdown_text or "").strip()
    native = native_concat.strip()
    if not markitdown:
        return False
    if markitdown == native:
        return False
    if markitdown in native:
        return False
    if len(markitdown) > 1.2 * len(native):
        return True
    return markitdown != native


def compose_pdf_markdown(
    *,
    pdf_path: Path,
    markitdown_text: str,
    ocr_pages: list[tuple[int, str]],
    tables: list[tuple[int, str]] | None = None,
) -> str:
    """Build interleaved page markdown from native text, OCR, and tables."""
    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    ocr_by_page = _group_by_page(ocr_pages)
    tables_by_page = _group_by_page(tables or [])

    page_sections: list[str] = []
    native_parts: list[str] = []

    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            page_num = page_index + 1
            section_parts = [f"## Page {page_num}"]

            native_text = _page_native_text(page)
            if len(native_text) >= min_chars:
                section_parts.append(native_text)
                native_parts.append(native_text)

            if page_num in ocr_by_page:
                for ocr_text in ocr_by_page[page_num]:
                    section_parts.append(f"### OCR\n\n{ocr_text}")

            if page_num in tables_by_page:
                section_parts.extend(tables_by_page[page_num])

            page_sections.append("\n\n".join(section_parts))

    result = "\n\n".join(page_sections)
    native_concat = "\n\n".join(native_parts)
    if _should_append_markitdown(markitdown_text, native_concat):
        markitdown = (markitdown_text or "").strip()
        suffix = f"## Document (MarkItDown)\n\n{markitdown}"
        if result:
            return f"{result}\n\n{suffix}"
        return suffix
    return result


def merge(
    markdown: str,
    pages: list[tuple[int, str]],
    *,
    pdf_path: Path | None = None,
    tables: list[tuple[int, str]] | None = None,
) -> str:
    if pdf_path is not None:
        return compose_pdf_markdown(
            pdf_path=pdf_path,
            markitdown_text=markdown,
            ocr_pages=pages,
            tables=tables,
        )

    if not pages:
        return markdown

    page_sections: list[str] = []
    for page_num, text in pages:
        page_sections.append(f"## Page {page_num}\n\n### OCR\n\n{text}")

    ocr_block = "\n\n".join(page_sections)
    base = (markdown or "").strip()
    if base:
        return f"{base}\n\n{ocr_block}"
    return ocr_block
