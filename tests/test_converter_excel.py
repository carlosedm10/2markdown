"""Tests for Excel conversion (twomarkdown.converter.excel)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from twomarkdown.converter.excel import (
    ExcelConversionError,
    convert_xlsx,
    rows_to_markdown_table,
)


class TestRowsToMarkdownTable:
    def test_rows_to_markdown_table_basic(self) -> None:
        table = rows_to_markdown_table([["A", "B"], ["1", "2"]])
        assert "| A | B |" in table
        assert "| --- | --- |" in table
        assert "| 1 | 2 |" in table


class TestConvertXlsx:
    def test_convert_xlsx_reads_sheets(self, tmp_path: Path) -> None:
        pytest.importorskip("openpyxl")
        from openpyxl import Workbook

        xlsx_path = tmp_path / "book.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws["A1"] = "Name"
        ws["B1"] = "Count"
        ws["A2"] = "alpha"
        ws["B2"] = 3
        wb.create_sheet("Empty")
        wb.save(xlsx_path)

        markdown = convert_xlsx(xlsx_path)

        assert "## Sheet: Data" in markdown
        assert "| Name | Count |" in markdown
        assert "| alpha | 3 |" in markdown
        assert "Empty" not in markdown

    def test_convert_xlsx_wide_sheet_uses_csv_block(self, tmp_path: Path) -> None:
        pytest.importorskip("openpyxl")
        from openpyxl import Workbook

        xlsx_path = tmp_path / "wide.xlsx"
        wb = Workbook()
        ws = wb.active
        for col in range(1, 32):
            ws.cell(row=1, column=col, value=f"c{col}")
        wb.save(xlsx_path)

        markdown = convert_xlsx(xlsx_path)

        assert "```csv" in markdown
        assert "c31" in markdown
        assert "| c1 |" not in markdown

    def test_convert_xlsx_missing_openpyxl_raises(self, tmp_path: Path) -> None:
        xlsx_path = tmp_path / "book.xlsx"
        xlsx_path.write_bytes(b"not-a-real-xlsx")

        with patch.dict("sys.modules", {"openpyxl": None}):
            with pytest.raises(ExcelConversionError, match="openpyxl"):
                convert_xlsx(xlsx_path)
