"""Tests for LibreOffice legacy routing."""

from pathlib import Path

from twomarkdown.converter.office_legacy import LEGACY_SUFFIXES, is_legacy_office


class TestLegacyOffice:
    def test_legacy_suffixes_include_open_document_and_xls(self) -> None:
        assert ".xls" in LEGACY_SUFFIXES
        assert ".odt" in LEGACY_SUFFIXES
        assert ".rtf" in LEGACY_SUFFIXES
        assert is_legacy_office(Path("memo.doc"))
        assert is_legacy_office(Path("sheet.xls"))
        assert not is_legacy_office(Path("modern.docx"))
