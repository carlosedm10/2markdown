"""Convert Apple Numbers (.numbers) documents to markdown.

Named ``numbers_doc`` (not ``numbers``) so it never shadows the
``numbers-parser`` package it imports, or the stdlib-adjacent ``numbers``
module.

numbers-parser reads the native document format directly — real sheets,
tables, cells, and (where Numbers cached one) formula text — so a .numbers
file no longer has to go through the iWork bundle's ``preview.pdf`` /
LibreOffice route, which flattens everything to prose and severs values
from their headers.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from twomarkdown.converter.excel import (
    MAX_GFM_COLUMNS,
    _rows_to_csv_block,
    rows_to_markdown_table,
)

if TYPE_CHECKING:
    from numbers_parser import Table

# A table with many formulas would otherwise dump an unreadable wall of text
# after the GFM table; cap the companion block and say what was cut, mirroring
# converter.excel's MAX_FORMULAS_PER_SHEET.
MAX_FORMULAS_PER_TABLE = 50

# numbers-parser docs: a formula's cached value is only available if Numbers
# itself had computed and saved it; otherwise the cell value comes back as
# this literal. Never surface it as if it were real data.
_UNCACHED_FORMULA_PLACEHOLDER = "*FORMULA*"


class NumbersConversionError(Exception):
    """Raised when Numbers conversion cannot be performed."""


def is_numbers_doc(path: Path) -> bool:
    """True if ``path`` looks like an Apple Numbers document by extension."""
    return Path(path).suffix.lower() == ".numbers"


def _format_cell_value(value: object) -> str:
    if value is None:
        return ""
    if value == _UNCACHED_FORMULA_PLACEHOLDER:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # Whole-number floats (6.0, common for scores/counts) should read as
        # "6", not "6.0". Numbers' own storage format re-serializes floats
        # with trailing binary noise (e.g. a written 12 comes back as
        # 12.000000000000002), so round before deciding it is an integer or
        # formatting it, rather than a strict float.is_integer() check.
        rounded = round(value, 9)
        if rounded == int(rounded):
            return str(int(rounded))
        return f"{rounded:.9f}".rstrip("0").rstrip(".")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).replace("\n", " ").strip()


def _table_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows():
        str_row = [_format_cell_value(cell.value) for cell in row]
        if any(str_row):
            rows.append(str_row)
    return rows


def _table_has_data(rows: list[list[str]]) -> bool:
    return any(any(cell for cell in row) for row in rows)


def _table_formulas(table: Table) -> list[tuple[str, str]]:
    """Collect (coordinate, formula text) ordered top-to-bottom, left-to-right."""
    from numbers_parser import xl_rowcol_to_cell

    formulas: list[tuple[str, str]] = []
    for row in table.rows():
        for cell in row:
            formula = getattr(cell, "formula", None)
            if not formula:
                continue
            coord = xl_rowcol_to_cell(cell.row, cell.col)
            formulas.append((coord, formula))
    return formulas


def _formula_block(formulas: list[tuple[str, str]]) -> str:
    if not formulas:
        return ""
    shown = formulas[:MAX_FORMULAS_PER_TABLE]
    omitted = len(formulas) - len(shown)
    entries = " · ".join(f"`{coord} = {text}`" for coord, text in shown)
    line = f"**Fórmulas:** {entries}"
    if omitted > 0:
        line += f" · _(+{omitted} fórmulas más omitidas)_"
    return line


def convert_numbers(path: Path) -> str:
    """Convert a .numbers document to markdown: one section per sheet/table."""
    try:
        from numbers_parser import Document
    except ImportError as exc:
        raise NumbersConversionError(
            "numbers-parser is required for Numbers conversion: "
            "pip install numbers-parser"
        ) from exc

    path = Path(path).resolve()

    try:
        document = Document(str(path))
    except Exception as exc:
        raise NumbersConversionError(f"numbers read failed: {exc}") from exc

    sections: list[str] = []

    for sheet in document.sheets:
        sheet_emitted = False
        for table in sheet.tables:
            rows = _table_rows(table)
            if not _table_has_data(rows):
                continue

            if not sheet_emitted:
                sections.append(f"## Sheet: {sheet.name}")
                sheet_emitted = True

            sections.append(f"### Table: {table.name}")

            width = max(len(row) for row in rows) if rows else 0
            if width > MAX_GFM_COLUMNS:
                sections.append(_rows_to_csv_block(rows))
            else:
                sections.append(rows_to_markdown_table(rows))
            sections.append("")

            block = _formula_block(_table_formulas(table))
            if block:
                sections.append(block)
                sections.append("")

    text = "\n".join(sections).strip()
    if not text:
        raise NumbersConversionError(
            f"numbers: no table data extracted from {path.name}"
        )
    return text
