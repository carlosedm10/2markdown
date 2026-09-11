"""Tests for Apple Numbers conversion (twomarkdown.converter.numbers_doc)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from twomarkdown.converter.numbers_doc import (
    NumbersConversionError,
    convert_numbers,
    is_numbers_doc,
)

# The real read-only fixture used only for the formula test (numbers-parser has
# no public API to write formulas, so a genuine Numbers-cached formula can only
# come from an actual document). Never modified; copied into tmp_path.
REAL_FIXTURE = Path(
    "/Users/carlos.dominguez/Library/Mobile Documents/com~apple~CloudDocs/"
    "Estudios/UPV/Notas UPV.numbers"
)


def _make_numbers_doc(path: Path) -> None:
    """Write a small synthetic .numbers workbook: one plain sheet, one wide sheet."""
    pytest.importorskip("numbers_parser")
    from numbers_parser import Document

    doc = Document()
    sheet = doc.sheets[0]
    sheet.name = "Grades"
    table = sheet.tables[0]
    table.name = "Scores"
    table.write(0, 0, "Name")
    table.write(0, 1, "Score")
    table.write(0, 2, "Date")
    table.write(1, 0, "alpha")
    table.write(1, 1, 6.0)
    table.write(1, 2, datetime(2024, 1, 15))
    table.write(2, 0, "beta")
    table.write(2, 1, 7.5)
    table.write(2, 2, datetime(2024, 2, 20))

    doc.add_sheet("Wide")
    wide_table = doc.sheets[1].tables[0]
    wide_table.name = "WideTable"
    wide_table.add_column(25)
    for col in range(wide_table.num_cols):
        wide_table.write(0, col, f"c{col}")

    doc.save(str(path))


class TestIsNumbersDoc:
    """is_numbers_doc() — extension sniffing."""

    def test_is_numbers_doc_true_for_numbers_suffix(self, tmp_path: Path) -> None:
        """A .numbers path is recognized regardless of case."""
        assert is_numbers_doc(tmp_path / "Sheet.numbers")
        assert is_numbers_doc(tmp_path / "Sheet.NUMBERS")

    def test_is_numbers_doc_false_for_other_suffix(self, tmp_path: Path) -> None:
        """Non-.numbers paths are rejected."""
        assert not is_numbers_doc(tmp_path / "book.xlsx")
        assert not is_numbers_doc(tmp_path / "notes.pages")


class TestConvertNumbers:
    """convert_numbers() — sheet/table markdown shape and cell formatting."""

    def test_convert_numbers_reads_sheets_and_tables(self, tmp_path: Path) -> None:
        """A named sheet/table pair renders as headed sections with a GFM table."""
        path = tmp_path / "book.numbers"
        _make_numbers_doc(path)

        markdown = convert_numbers(path)

        assert "## Sheet: Grades" in markdown
        assert "### Table: Scores" in markdown
        assert "| Name | Score | Date |" in markdown
        assert "| alpha |" in markdown
        assert "| beta |" in markdown

    def test_convert_numbers_whole_number_float_has_no_decimal(
        self, tmp_path: Path
    ) -> None:
        """A whole-number float cell (6.0) renders as '6', not '6.0'."""
        path = tmp_path / "book.numbers"
        _make_numbers_doc(path)

        markdown = convert_numbers(path)

        assert "| alpha | 6 |" in markdown
        assert "6.0" not in markdown

    def test_convert_numbers_fractional_float_keeps_decimal(
        self, tmp_path: Path
    ) -> None:
        """A non-integer float cell (7.5) keeps its fractional part."""
        path = tmp_path / "book.numbers"
        _make_numbers_doc(path)

        markdown = convert_numbers(path)

        assert "7.5" in markdown

    def test_convert_numbers_date_renders_iso(self, tmp_path: Path) -> None:
        """A datetime cell renders as an ISO 8601 string."""
        path = tmp_path / "book.numbers"
        _make_numbers_doc(path)

        markdown = convert_numbers(path)

        assert "2024-01-15" in markdown
        assert "2024-02-20" in markdown

    def test_convert_numbers_wide_table_uses_csv_block(self, tmp_path: Path) -> None:
        """A table wider than MAX_GFM_COLUMNS falls back to the shared CSV block."""
        path = tmp_path / "book.numbers"
        _make_numbers_doc(path)

        markdown = convert_numbers(path)

        assert "## Sheet: Wide" in markdown
        assert "### Table: WideTable" in markdown
        assert "```csv" in markdown
        assert "c30" in markdown
        assert "| c0 |" not in markdown

    def test_convert_numbers_missing_dependency_raises(self, tmp_path: Path) -> None:
        """Without numbers-parser installed, NumbersConversionError names it."""
        path = tmp_path / "book.numbers"
        path.write_bytes(b"not-a-real-numbers-file")

        with patch.dict("sys.modules", {"numbers_parser": None}):
            with pytest.raises(NumbersConversionError, match="numbers-parser"):
                convert_numbers(path)

    def test_convert_numbers_empty_document_raises(self, tmp_path: Path) -> None:
        """A document with no populated cells raises instead of emitting nothing."""
        pytest.importorskip("numbers_parser")
        from numbers_parser import Document

        path = tmp_path / "empty.numbers"
        Document().save(str(path))

        with pytest.raises(NumbersConversionError, match="no table data"):
            convert_numbers(path)

    def test_convert_numbers_unreadable_file_raises(self, tmp_path: Path) -> None:
        """A file that isn't a real Numbers document raises NumbersConversionError."""
        pytest.importorskip("numbers_parser")
        path = tmp_path / "bad.numbers"
        path.write_bytes(b"not-a-real-numbers-file")

        with pytest.raises(NumbersConversionError, match="numbers read failed"):
            convert_numbers(path)


class TestConvertNumbersFormulas:
    """convert_numbers() — formula block, against a real user document.

    numbers-parser has no public API for writing formulas (Numbers itself
    computes and caches them), so this is the only way to exercise real
    formula text end to end. Skipped when the fixture isn't present, and the
    original file is only ever read, never modified.
    """

    @pytest.mark.skipif(
        not REAL_FIXTURE.is_file(), reason="real Numbers fixture not present"
    )
    def test_convert_numbers_real_workbook_includes_formula_block(
        self, tmp_path: Path
    ) -> None:
        """A workbook with real formulas produces a compact 'Fórmulas:' block."""
        copy_path = tmp_path / REAL_FIXTURE.name
        copy_path.write_bytes(REAL_FIXTURE.read_bytes())

        markdown = convert_numbers(copy_path)

        assert "## Sheet:" in markdown
        assert "### Table:" in markdown
        # numbers-parser returns the literal "*FORMULA*" for a formula whose
        # cached value isn't available; it must never leak into the output.
        assert "*FORMULA*" not in markdown
        assert "**Fórmulas:**" in markdown
