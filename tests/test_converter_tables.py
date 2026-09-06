"""Test cases for PDF table extraction (src.converter.tables)."""

from pathlib import Path
from unittest.mock import patch

import fitz

from src.converter import tables


def _make_table_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    rects = [
        (72, 72, 200, 100),
        (200, 72, 328, 100),
        (72, 100, 200, 128),
        (200, 100, 328, 128),
    ]
    for rect in rects:
        page.draw_rect(fitz.Rect(*rect))
    page.insert_text((80, 90), "A1")
    page.insert_text((210, 90), "B1")
    page.insert_text((80, 118), "A2")
    page.insert_text((210, 118), "B2")
    doc.save(path)
    doc.close()
    return path


class TestRowsToMarkdownTable:
    """Test cases for rows_to_markdown_table()."""

    def test_rows_to_markdown_table_empty_input(self) -> None:
        """rows_to_markdown_table() — empty rows return an empty string."""
        assert tables.rows_to_markdown_table([]) == ""

    def test_rows_to_markdown_table_formats_gfm_table(self) -> None:
        """rows_to_markdown_table() — first row is header with separator."""
        result = tables.rows_to_markdown_table(
            [
                ["Name", "Value"],
                ["alpha", "1"],
            ]
        )

        expected_lines = [
            "| Name | Value |",
            "| --- | --- |",
            "| alpha | 1 |",
        ]
        assert result == "\n".join(expected_lines)

    def test_rows_to_markdown_table_escapes_pipes(self) -> None:
        """rows_to_markdown_table() — pipe characters in cells are escaped."""
        result = tables.rows_to_markdown_table([["a|b", "c"]])

        assert "| a\\|b | c |" in result


class TestExtractPdfTables:
    """Test cases for extract_pdf_tables()."""

    def test_extract_pdf_tables_disabled_returns_empty(self, tmp_path: Path) -> None:
        """extract_pdf_tables() — returns [] when extract_tables is disabled."""
        pdf_path = _make_table_pdf(tmp_path / "table.pdf")

        with patch("src.converter.tables.conversion_config") as mock_config:
            mock_config.extract_tables = False
            assert tables.extract_pdf_tables(pdf_path) == []

    def test_extract_pdf_tables_on_simple_pdf(self, tmp_path: Path) -> None:
        """extract_pdf_tables() — returns list for a PDF (tables optional)."""
        pdf_path = _make_table_pdf(tmp_path / "table.pdf")

        with patch("src.converter.tables.conversion_config") as mock_config:
            mock_config.extract_tables = True
            results = tables.extract_pdf_tables(pdf_path)

        assert isinstance(results, list)
        if results:
            page_num, markdown = results[0]
            assert page_num == 1
            assert "### Table (page 1)" in markdown
            assert "| A1 | B1 |" in markdown

    def test_extract_pdf_tables_wraps_rows_from_mock_extractor(
        self, tmp_path: Path
    ) -> None:
        """extract_pdf_tables() — wraps extracted rows as page-scoped markdown."""
        pdf_path = _make_table_pdf(tmp_path / "mocked.pdf")
        mock_rows = [["Col1", "Col2"], ["x", "y"]]
        expected_table = tables.rows_to_markdown_table(mock_rows)

        class FakeTable:
            def extract(self) -> list[list[str]]:
                return mock_rows

        class FakeFinder:
            tables = [FakeTable()]

        with (
            patch("src.converter.tables.conversion_config") as mock_config,
            patch("fitz.open") as mock_open,
        ):
            mock_config.extract_tables = True
            mock_page = mock_open.return_value.__enter__.return_value.__getitem__.return_value
            mock_page.find_tables.return_value = FakeFinder()
            mock_open.return_value.__enter__.return_value.__iter__ = lambda self: iter(
                [mock_page]
            )

            results = tables.extract_pdf_tables(pdf_path)

        assert results == [(1, f"### Table (page 1)\n\n{expected_table}")]
