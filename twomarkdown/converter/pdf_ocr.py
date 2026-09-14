"""Scanned PDF fallback: rasterize pages and OCR with Tesseract or Ollama."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import fitz
from tqdm import tqdm

from twomarkdown.batch import events
from twomarkdown.config import pdf_ocr_config
from twomarkdown.telemetry import span

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"\S+")


def is_scrambled_text(text: str) -> bool:
    """True when a page's text layer is shredded rather than merely short.

    Slides built with equation objects extract as loose tokens — "Tol x f x x Tol
    x f Tol x x k k k k" — which is unreadable but long, so the character-count
    test passes it through untouched. A high share of single-character tokens is
    the signature; the threshold sits above every readable page measured.
    """
    tokens = _TOKEN_RE.findall(text or "")
    if len(tokens) < pdf_ocr_config.pdf_text_scramble_min_tokens:
        return False
    singles = sum(1 for token in tokens if len(token) == 1)
    return singles / len(tokens) > pdf_ocr_config.pdf_text_scramble_ratio


def _vision_model_available() -> bool:
    from twomarkdown.config import conversion_config, llm_config

    return bool(llm_config.llm_enabled and conversion_config.ocr_backend == "ollama")


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
                page_text = page.get_text().strip()
                if len(page_text) < min_chars or is_scrambled_text(page_text):
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


def _current_ocr_engine_name() -> str:
    """The model that will actually answer a page, for the `page_done` event."""
    if not _vision_model_available():
        return "tesseract"
    from twomarkdown.agents.image_ocr import normalize_model_id
    from twomarkdown.config import llm_config

    return normalize_model_id(llm_config.vision_model)


def extract_pages(
    pdf_path: Path,
    *,
    ocr_fn: Callable[[bytes], str],
    show_progress: bool = False,
    doc: fitz.Document | None = None,
    cancel: threading.Event | None = None,
    file_index: int | None = None,
) -> list[tuple[int, str]]:
    """OCR PDF pages with insufficient native text; returns (page_number, text).

    ``file_index`` is this file's position in the running job's file list (see
    `batch/events.py`); when set, one `page_started`/`page_done` pair is
    emitted per page actually OCR'd, for the desktop app's live view. `None`
    (the CLI's own calls) emits nothing — same no-op-when-unattached rule as
    everywhere else in `batch/events.py`.
    """
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
            if cancel is not None and cancel.is_set():
                # Hand back the pages already transcribed instead of discarding
                # them: the caller writes a partial file rather than nothing.
                logger.warning(
                    "Cancelled after %s page(s) of %s; keeping partial OCR",
                    len(results),
                    pdf_path.name,
                )
                break
            page = opened[i]
            page_text = page.get_text().strip()
            scrambled = is_scrambled_text(page_text)
            if len(page_text) >= min_chars and not scrambled:
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
            if cancel is not None and cancel.is_set():
                break
            if file_index is not None:
                events.page_started(file_index, page_num)
            page_started_at = time.perf_counter()
            fallback_before = ocr_mod.engine_fallback_count()
            with (
                events.stage("ocr", page=page_num),
                span("pdf.page_ocr", page=page_num),
            ):
                text = ocr_mod.ocr_image_bytes(
                    png_bytes,
                    ocr_fn=ocr_fn,
                    llm_min_confidence=pdf_ocr_config.pdf_ocr_llm_min_confidence,
                    # Tesseract's confidence does not track whether the maths or
                    # the handwriting survived: on this corpus its median was 73
                    # against a threshold of 75, so it vetoed the vision model at
                    # random and 77 pages came out as noise. When a vision model
                    # is configured it does the page; Tesseract is the fallback
                    # for when there is no model, not a gatekeeper for it.
                    force_llm=scrambled or _vision_model_available(),
                    cancel=cancel,
                ).strip()
            fell_back = ocr_mod.engine_fallback_count() > fallback_before
            review_changes: list[dict[str, object]] = []
            if text:
                text, review_changes = _reviewed(text, pdf_path, page_num)
                results.append((page_num, text))
            if file_index is not None:
                events.page_done(
                    file_index,
                    page_num,
                    engine="tesseract" if fell_back else _current_ocr_engine_name(),
                    seconds=time.perf_counter() - page_started_at,
                    fallback=fell_back,
                    review_changes=review_changes,
                )

    return results


def _reviewed(
    text: str, pdf_path: Path, page_num: int
) -> tuple[str, list[dict[str, object]]]:
    """Proofread one page's transcription, if a reviewer is configured.

    Text only: the pass reads what was written and fixes what the writing itself
    contradicts. It is given no image, so it cannot tell what the page holds that
    the transcription does not — and therefore cannot invent it.

    Returns the (possibly corrected) text and the diff structured for
    `EventPageDone.review_changes` — the same real diff `describe_changes()`
    already logs, not the model's own account of what it fixed.
    """
    from twomarkdown.agents.page_review import (
        describe_changes_structured,
        review_enabled,
        review_page,
    )

    if not review_enabled():
        return text, []
    with events.stage("review", page=page_num), span("pdf.page_review", page=page_num):
        reviewed, changes = review_page(text)
    if changes:
        logger.info(
            "Review corrected %s page %s: %s",
            pdf_path.name,
            page_num,
            "; ".join(changes[:5]),
        )
    structured = describe_changes_structured(text, reviewed) if changes else []
    return reviewed, structured


def _word_gaps(words: list) -> tuple[dict, list[float]]:
    """Group words into lines and measure each inter-word gap, in font heights."""
    by_line: dict = {}
    for w in words:
        by_line.setdefault((w[5], w[6]), []).append(w)
    gaps: list[float] = []
    for line in by_line.values():
        line.sort(key=lambda w: w[0])
        for a, b in zip(line, line[1:], strict=False):
            height = a[3] - a[1]
            if height > 0:
                gaps.append((b[0] - a[2]) / height)
    return by_line, sorted(gaps)


def _quantile(values: list[float], q: float) -> float:
    return values[min(int(len(values) * q), len(values) - 1)]


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _short_token_ratio(text: str) -> float | None:
    """Share of very short word tokens — the symptom of mid-word splitting."""
    tokens = _WORD_RE.findall(text or "")
    if len(tokens) < 20:
        return None
    return sum(1 for t in tokens if len(t) <= 4) / len(tokens)


def repair_fragmented_page_text(page: fitz.Page) -> str | None:
    """Rejoin words that a renderer split mid-glyph, or None if the page is fine.

    LibreOffice renders some Keynote text with per-character positioning, so the
    extractor reads "Eval uaci ón de Met odol ogí as". The spurious gaps are
    narrower than real spaces, giving two distinct gap populations; an ordinary
    page has only one. Detect that split, then join across the narrow gaps.

    Only spaces are removed — no character is ever changed — so the text cannot
    gain a meaning the page did not have.
    """
    try:
        words = page.get_text("words")
    except Exception:
        return None
    if len(words) < pdf_ocr_config.pdf_fragment_min_words:
        return None

    # Guard 1, the symptom: real prose is not mostly three-letter fragments.
    symptom = _short_token_ratio(page.get_text())
    if symptom is None or symptom < pdf_ocr_config.pdf_fragment_short_tokens:
        return None
    return rejoin_words(words)


def rejoin_words(words: list) -> str | None:
    """Join words split mid-glyph, from PyMuPDF "words" tuples. None if not needed.

    Kept separate from the page so the rule is testable without rendering a PDF:
    PyMuPDF merges very close pieces during extraction, so a synthetic fixture
    cannot reproduce the condition this repairs.
    """
    by_line, gaps = _word_gaps(words)
    if len(gaps) < pdf_ocr_config.pdf_fragment_min_words:
        return None

    # Guard 2, the geometry: spurious gaps form a second, narrower population.
    narrow, wide = _quantile(gaps, 0.20), _quantile(gaps, 0.80)
    if narrow <= 0 or wide / narrow < pdf_ocr_config.pdf_fragment_gap_ratio:
        return None  # one population: ordinary spacing, leave it alone

    threshold = (_quantile(gaps, 0.35) + wide) / 2
    lines: list[str] = []
    for key in sorted(by_line, key=lambda k: (by_line[k][0][1], by_line[k][0][0])):
        parts = sorted(by_line[key], key=lambda w: w[0])
        text = parts[0][4]
        for a, b in zip(parts, parts[1:], strict=False):
            height = a[3] - a[1]
            gap = (b[0] - a[2]) / height if height > 0 else threshold + 1
            joinable = (
                len(a[4]) <= pdf_ocr_config.pdf_fragment_max_piece
                and len(b[4]) <= pdf_ocr_config.pdf_fragment_max_piece
            )
            text += ("" if gap < threshold and joinable else " ") + b[4]
        lines.append(text)
    return "\n".join(lines).strip() or None


def _native_page_text(page: fitz.Page, min_chars: int) -> str:
    repaired = repair_fragmented_page_text(page)
    if repaired is not None and len(repaired) >= min_chars:
        return repaired
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
            # A scrambled text layer is noise once the model has transcribed the
            # page; keeping both would leave the garbage above the good version.
            if native and page_num in ocr_map and is_scrambled_text(native):
                native = ""
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
    # Only a safety net for when per-page extraction found almost nothing. The old
    # "MarkItDown is 1.2x longer" test fired on ordinary decks and appended a second
    # copy of the whole document, full of MarkItDown's own malformed tables.
    thin_native = len(native_joined) < 200
    if mid and thin_native and len(mid) > len(native_joined) and mid not in body:
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
