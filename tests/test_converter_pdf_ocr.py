"""Test cases for scanned PDF OCR fallback (src.converter.pdf_ocr)."""

from src.converter import pdf_ocr


class TestPdfOcrFallback:
    """Test cases for should_fallback() and merge()."""

    # -------------------------------------------------------------------------
    # Trigger conditions
    # -------------------------------------------------------------------------

    def test_should_fallback_for_empty_or_short_pdf_text(self) -> None:
        """should_fallback() — True for PDF when text length < pdf_ocr_min_chars."""
        assert pdf_ocr.should_fallback("", suffix=".pdf") is True
        assert pdf_ocr.should_fallback("short", suffix=".pdf") is True
        assert pdf_ocr.should_fallback("x" * 100, suffix=".pdf") is False

    def test_should_not_fallback_for_non_pdf(self) -> None:
        """should_fallback() — False for non-PDF suffix regardless of text length."""
        assert pdf_ocr.should_fallback("", suffix=".txt") is False

    # -------------------------------------------------------------------------
    # Merge
    # -------------------------------------------------------------------------

    def test_merge_prepends_existing_text_and_adds_ocr_sections(self) -> None:
        """merge() — keeps MarkItDown text and appends Scanned pages (OCR fallback)."""
        merged = pdf_ocr.merge("Existing text", [(1, "Page one")])

        expected_fragments = [
            "Existing text",
            "Scanned pages (OCR fallback)",
            "## Page 1 — OCR",
            "Page one",
        ]
        for fragment in expected_fragments:
            assert fragment in merged

    def test_merge_returns_only_ocr_when_markitdown_text_empty(self) -> None:
        """merge() — OCR-only output when MarkItDown returned no text."""
        merged = pdf_ocr.merge("", [(1, "Only OCR")])

        assert "Only OCR" in merged
        assert "Scanned pages" not in merged
