"""PDF table extraction helpers."""

import logging
from pathlib import Path

import fitz

from src.config import conversion_config

logger = logging.getLogger(__name__)


def rows_to_markdown_table(rows: list[list[str]]) -> str:
    """Convert table rows to a GitHub-flavored markdown table."""
    if not rows:
        return ""

    normalized: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        normalized.append([str(cell or "").replace("|", "\\|") for cell in row])

    if not normalized:
        return ""

    num_cols = max(len(row) for row in normalized)
    padded = [row + [""] * (num_cols - len(row)) for row in normalized]

    header = padded[0]
    separator = ["---"] * num_cols
    body = padded[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def extract_pdf_tables(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract tables from a PDF as (page_number, markdown) tuples."""
    if not conversion_config.extract_tables:
        return []

    results: list[tuple[int, str]] = []

    try:
        with fitz.open(pdf_path) as doc:
            for page_index, page in enumerate(doc):
                page_num = page_index + 1
                try:
                    table_finder = page.find_tables()
                    page_tables = table_finder.tables if table_finder else []
                except Exception:
                    logger.exception(
                        "Table detection failed on page %s of %s",
                        page_num,
                        pdf_path.name,
                    )
                    continue

                for table in page_tables:
                    try:
                        rows = table.extract()
                    except Exception:
                        logger.exception(
                            "Table extraction failed on page %s of %s",
                            page_num,
                            pdf_path.name,
                        )
                        continue

                    markdown_table = rows_to_markdown_table(rows)
                    if not markdown_table:
                        continue

                    block = f"### Table (page {page_num})\n\n{markdown_table}"
                    results.append((page_num, block))
    except Exception:
        logger.exception("Failed to extract tables from %s", pdf_path)

    return results
