"""Table extraction helpers for PDF and shared GFM rendering."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from twomarkdown.config import conversion_config

logger = logging.getLogger(__name__)


def rows_to_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines: list[str] = []
    for i, row in enumerate(normalized):
        line = "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"
        lines.append(line)
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


# A slide's outer frame is ruled, so find_tables() reports the whole slide as a
# one-cell table and the page text gets duplicated inside it. Real tables have
# several populated columns and short cells.
MAX_CELL_CHARS = 200
# A real table is densely populated; a bulleted list that LibreOffice laid out in
# columns leaves most cells empty. Measured: real tables ~92% full, list layouts ~25%.
MIN_CELL_FILL_RATIO = 0.5
MIN_POPULATED_COLUMNS = 2
MIN_ROWS = 2
MAX_PAGE_AREA_RATIO = 0.8


def _populated_columns(rows: list[list[str]]) -> int:
    width = max((len(row) for row in rows), default=0)
    count = 0
    for col in range(width):
        filled = sum(1 for row in rows if col < len(row) and row[col])
        if filled >= MIN_ROWS:
            count += 1
    return count


def _cell_fill_ratio(rows: list[list[str]]) -> float:
    cells = [cell for row in rows for cell in row]
    if not cells:
        return 0.0
    return sum(1 for cell in cells if cell) / len(cells)


def is_real_table(rows: list[list[str]], *, area_ratio: float | None = None) -> bool:
    """Reject slide frames, figure borders and list layouts reported as tables."""
    if len(rows) < MIN_ROWS:
        return False
    if _cell_fill_ratio(rows) < MIN_CELL_FILL_RATIO:
        return False
    if _populated_columns(rows) < MIN_POPULATED_COLUMNS:
        return False
    if any(len(cell) > MAX_CELL_CHARS for row in rows for cell in row):
        return False
    if area_ratio is not None and area_ratio > MAX_PAGE_AREA_RATIO:
        return False
    return True


def _area_ratio(table: object, page: object) -> float | None:
    try:
        x0, y0, x1, y1 = table.bbox  # type: ignore[attr-defined]
        page_rect = page.rect  # type: ignore[attr-defined]
        page_area = float(page_rect.width) * float(page_rect.height)
        if page_area <= 0:
            return None
        return (float(x1) - float(x0)) * (float(y1) - float(y0)) / page_area
    except Exception:
        return None


def extract_pdf_tables(
    pdf_path: Path, *, doc: fitz.Document | None = None
) -> list[tuple[int, str]]:
    """Return (page_number, markdown) tables. Never raises."""
    if not conversion_config.extract_tables:
        return []

    from twomarkdown.converter.pdf_ocr import open_pdf

    results: list[tuple[int, str]] = []
    try:
        with open_pdf(pdf_path, doc) as opened:
            for index, page in enumerate(opened):
                page_num = index + 1
                finder = getattr(page, "find_tables", None)
                if finder is None:
                    continue
                try:
                    found = finder()
                except Exception as exc:
                    logger.debug("find_tables failed page %s: %s", page_num, exc)
                    continue
                tables = getattr(found, "tables", found) or []
                # find_tables() ordering is not stable between runs; sort by
                # position so repeated conversions produce identical markdown.
                try:
                    tables = sorted(
                        tables, key=lambda t: (round(t.bbox[1], 1), round(t.bbox[0], 1))
                    )
                except Exception:
                    pass
                for table in tables:
                    try:
                        raw_rows = table.extract()
                    except Exception as exc:
                        logger.debug("table.extract failed page %s: %s", page_num, exc)
                        continue
                    rows = [[_cell_text(c) for c in row] for row in (raw_rows or [])]
                    if not any(any(cell for cell in row) for row in rows):
                        continue
                    if not is_real_table(rows, area_ratio=_area_ratio(table, page)):
                        logger.debug("Rejected non-table region on page %s", page_num)
                        continue
                    md = rows_to_markdown_table(rows)
                    if not md:
                        continue
                    block = f"### Table (page {page_num})\n\n{md}"
                    results.append((page_num, block))
    except Exception as exc:
        logger.warning("PDF table extraction failed for %s: %s", pdf_path, exc)
    return results
