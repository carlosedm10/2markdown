"""Convert Excel .xlsx workbooks to markdown."""

from __future__ import annotations

import csv
import io
from pathlib import Path

MAX_GFM_COLUMNS = 30


class ExcelConversionError(Exception):
    """Raised when Excel conversion cannot be performed."""


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


def _rows_to_csv_block(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    return f"```csv\n{buffer.getvalue().rstrip()}\n```"


def _cell_to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def _sheet_has_data(rows: list[list[str]]) -> bool:
    return any(any(cell for cell in row) for row in rows)


def convert_xlsx(path: Path) -> str:
    """Convert an .xlsx workbook to markdown with one section per sheet."""
    try:
        import openpyxl
    except ImportError as exc:
        raise ExcelConversionError(
            "openpyxl is required for Excel conversion: pip install openpyxl"
        ) from exc

    path = path.resolve()
    sections: list[str] = []

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ExcelConversionError(f"xlsx read failed: {exc}") from exc

    try:
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                str_row = [_cell_to_str(cell) for cell in row]
                if any(str_row):
                    rows.append(str_row)

            if not _sheet_has_data(rows):
                continue

            sections.append(f"## Sheet: {sheet.title}")

            width = max(len(row) for row in rows) if rows else 0
            if width > MAX_GFM_COLUMNS:
                sections.append(_rows_to_csv_block(rows))
            else:
                sections.append(rows_to_markdown_table(rows))
            sections.append("")
    finally:
        workbook.close()

    text = "\n".join(sections).strip()
    if not text:
        raise ExcelConversionError("xlsx: no sheet data extracted")
    return text
