"""Scanned PDF fallback: rasterize pages and OCR with Tesseract or Ollama."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import fitz
from tqdm import tqdm

from twomarkdown.config import pdf_ocr_config
from twomarkdown.telemetry import span

logger = logging.getLogger(__name__)


def _raise_if_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        from twomarkdown.converter.markitdown_converter import ConversionError

        raise ConversionError("cancelled")


@contextmanager
def open_pdf(
    pdf_path: Path, doc: fitz.Document | None = None
) -> Iterator[fitz.Document]:
    """Yield an open PDF, reusing ``doc`` when the caller already holds one."""
    if doc is not None:
        yield doc
        return
    opened = fitz.open(pdf_path)
    try:
        yield opened
    finally:
        opened.close()


def should_fallback(
    markdown: str,
    *,
    suffix: str = "",
    pdf_path: Path | None = None,
    doc: fitz.Document | None = None,
) -> bool:
    if not pdf_ocr_config.pdf_ocr_enabled:
        return False
    if suffix.lower() != ".pdf":
        return False
    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    if pdf_path is not None or doc is not None:
        path = pdf_path if pdf_path is not None else Path(".")
        with open_pdf(path, doc) as opened:
            for page in opened:
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
    try:
        return pix.tobytes("png")
    finally:
        pix = None


def extract_pages(
    pdf_path: Path,
    *,
    ocr_fn: Callable[[bytes], str],
    show_progress: bool = False,
    doc: fitz.Document | None = None,
    cancel: threading.Event | None = None,
) -> list[tuple[int, str]]:
    """OCR PDF pages with insufficient native text; returns (page_number, text)."""
    from twomarkdown.converter import ocr as ocr_mod

    results: list[tuple[int, str]] = []
    max_pages = pdf_ocr_config.pdf_ocr_max_pages
    min_chars = pdf_ocr_config.pdf_ocr_min_chars

    with open_pdf(pdf_path, doc) as opened:
        page_count = opened.page_count
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
            _raise_if_cancelled(cancel)
            page = opened[i]
            if len(page.get_text().strip()) >= min_chars:
                continue

            page_num = i + 1
            if not show_progress:
                logger.debug(
                    "PDF page OCR %s/%s: %s",
                    page_num,
                    limit,
                    pdf_path.name,
                )
            png_bytes = _render_page_pixmap(opened, i)
            _raise_if_cancelled(cancel)
            with span("pdf.page_ocr", page=page_num):
                text = ocr_mod.ocr_image_bytes(
                    png_bytes,
                    ocr_fn=ocr_fn,
                    llm_if_empty_only=True,
                    cancel=cancel,
                ).strip()
            if text:
                results.append((page_num, text))

    return results


def _native_page_text(page: fitz.Page, min_chars: int) -> str:
    try:
        blocks = page.get_text("blocks")
    except Exception:
        blocks = None
    if blocks:
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
        text = "\n".join(
            str(b[4]).strip() for b in ordered if len(b) > 4 and str(b[4]).strip()
        )
        if len(text.strip()) >= min_chars:
            return text.strip()
    text = page.get_text().strip()
    if len(text) >= min_chars:
        return text
    return ""


def _format_ocr_prose(page_num: int, text: str) -> str:
    return f"## Page {page_num}\n\n### OCR\n\n{text.strip()}"


def merge(markdown: str, pages: list[tuple[int, str]]) -> str:
    """Append OCR pages as prose (no code fences), in page order."""
    if not pages:
        return markdown or ""
    sections = [_format_ocr_prose(num, text) for num, text in pages]
    ocr_body = "\n\n".join(sections)
    base = (markdown or "").strip()
    if base:
        return f"{base}\n\n{ocr_body}"
    return ocr_body


def compose_pdf_markdown(
    *,
    pdf_path: Path,
    markitdown_text: str,
    ocr_pages: list[tuple[int, str]],
    tables: list[tuple[int, str]] | None = None,
    doc: fitz.Document | None = None,
) -> str:
    """Build markdown in page order: native text, OCR prose, tables."""
    ocr_map = {num: text for num, text in ocr_pages}
    table_map: dict[int, list[str]] = {}
    for num, block in tables or []:
        table_map.setdefault(num, []).append(block)

    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    parts: list[str] = []
    native_concat: list[str] = []

    with open_pdf(pdf_path, doc) as opened:
        for index, page in enumerate(opened):
            page_num = index + 1
            chunks = [f"## Page {page_num}"]
            native = _native_page_text(page, min_chars)
            if native:
                chunks.append(native)
                native_concat.append(native)
            if page_num in ocr_map:
                chunks.append("### OCR")
                chunks.append(ocr_map[page_num].strip())
            for table_md in table_map.get(page_num, []):
                chunks.append(table_md)
            parts.append("\n\n".join(chunks))

    body = "\n\n".join(parts).strip()
    mid = (markitdown_text or "").strip()
    native_joined = "\n\n".join(native_concat).strip()
    if mid and len(mid) > max(len(native_joined) * 1.2, 80) and mid not in body:
        extra = f"## Document (MarkItDown)\n\n{mid}"
        if body:
            return f"{body}\n\n{extra}"
        return extra
    return body or mid or merge(mid, ocr_pages)


def pdf_meta(
    source: Path, doc: fitz.Document | None = None
) -> tuple[str | None, int | None]:
    """Return (title, page_count) from PDF metadata."""
    try:
        with open_pdf(source, doc) as opened:
            meta = opened.metadata or {}
            title = (meta.get("title") or "").strip() or None
            return title, opened.page_count
    except Exception:
        return None, None
