"""Table extraction helpers for PDF and shared GFM rendering."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from src.config import conversion_config

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


def extract_pdf_tables(pdf_path: Path) -> list[tuple[int, str]]:
    """Return (page_number, markdown) tables. Never raises."""
    if not conversion_config.extract_tables:
        return []

    results: list[tuple[int, str]] = []
    try:
        with fitz.open(pdf_path) as doc:
            for index, page in enumerate(doc):
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
                for table in tables:
                    try:
                        raw_rows = table.extract()
                    except Exception as exc:
                        logger.debug("table.extract failed page %s: %s", page_num, exc)
                        continue
                    rows = [[_cell_text(c) for c in row] for row in (raw_rows or [])]
                    if not any(any(cell for cell in row) for row in rows):
                        continue
                    md = rows_to_markdown_table(rows)
                    if not md:
                        continue
                    block = f"### Table (page {page_num})\n\n{md}"
                    results.append((page_num, block))
    except Exception as exc:
        logger.warning("PDF table extraction failed for %s: %s", pdf_path, exc)
    return results
