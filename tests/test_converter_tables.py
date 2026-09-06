"""Tests for PDF/GFM table helpers."""

from pathlib import Path
from unittest.mock import patch

import fitz

from twomarkdown.converter.tables import extract_pdf_tables, rows_to_markdown_table


class TestRowsToMarkdownTable:
    def test_rows_to_markdown_table_escapes_pipes(self) -> None:
        table = rows_to_markdown_table([["a|b", "c"], ["1", "2"]])
        assert "| a\\|b | c |" in table
        assert "| --- | --- |" in table


class TestExtractPdfTables:
    def test_extract_pdf_tables_returns_empty_when_disabled(
        self, tmp_path: Path
    ) -> None:
        doc = fitz.open()
        doc.new_page()
        path = tmp_path / "t.pdf"
        doc.save(path)
        doc.close()
        with patch(
            "twomarkdown.converter.tables.conversion_config.extract_tables",
            False,
        ):
            assert extract_pdf_tables(path) == []

    def test_extract_pdf_tables_never_raises_on_plain_pdf(self, tmp_path: Path) -> None:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "hello")
        path = tmp_path / "plain.pdf"
        doc.save(path)
        doc.close()
        assert extract_pdf_tables(path) == [] or isinstance(
            extract_pdf_tables(path), list
        )
