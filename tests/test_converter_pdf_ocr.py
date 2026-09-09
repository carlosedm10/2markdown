"""Test cases for scanned PDF OCR fallback (twomarkdown.converter.pdf_ocr)."""

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import fitz
import pytest

from twomarkdown.config import pdf_ocr_config
from twomarkdown.converter import pdf_ocr
from twomarkdown.converter.markitdown_converter import ConversionError

LONG_TEXT = "x" * pdf_ocr_config.pdf_ocr_min_chars
SHORT_TEXT = "scan"


def _make_pdf(path: Path, page_texts: list[str]) -> Path:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


class TestPdfOcrFallback:
    """Test cases for should_fallback() and merge()."""

    # -------------------------------------------------------------------------
    # Trigger conditions (markdown-only, backward compat)
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
    # Per-page fallback (pdf_path)
    # -------------------------------------------------------------------------

    def test_should_fallback_true_when_any_page_lacks_native_text(
        self, tmp_path: Path
    ) -> None:
        """should_fallback() — True when any page has native text < min_chars."""
        mixed = _make_pdf(tmp_path / "mixed.pdf", [LONG_TEXT, ""])
        assert (
            pdf_ocr.should_fallback(
                LONG_TEXT,
                suffix=".pdf",
                pdf_path=mixed,
            )
            is True
        )

    def test_should_fallback_false_for_fully_digital_pdf(self, tmp_path: Path) -> None:
        """should_fallback() — False when every page has enough native text."""
        digital = _make_pdf(tmp_path / "digital.pdf", [LONG_TEXT])
        assert (
            pdf_ocr.should_fallback(
                LONG_TEXT,
                suffix=".pdf",
                pdf_path=digital,
            )
            is False
        )

    # -------------------------------------------------------------------------
    # extract_pages — selective OCR
    # -------------------------------------------------------------------------

    def test_extract_pages_ocrs_only_pages_with_insufficient_native_text(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — OCR only pages below min_chars; mixed PDF."""
        mixed = _make_pdf(tmp_path / "mixed.pdf", [LONG_TEXT, ""])
        ocr_fn = MagicMock(return_value="OCR-P2")

        with patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", False):
            pages = pdf_ocr.extract_pages(mixed, ocr_fn=ocr_fn)

        ocr_fn.assert_called_once()
        assert pages == [(2, "OCR-P2")]

        merged = pdf_ocr.merge("Digital layer from MarkItDown", pages)
        assert "Digital layer from MarkItDown" in merged
        assert "OCR-P2" in merged
        assert "Scanned pages" not in merged

    def test_extract_pages_skips_fully_digital_pdf(self, tmp_path: Path) -> None:
        """extract_pages() — no OCR when every page has enough native text."""
        digital = _make_pdf(tmp_path / "digital.pdf", [LONG_TEXT])
        ocr_fn = MagicMock(return_value="should not run")

        with patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", False):
            pages = pdf_ocr.extract_pages(digital, ocr_fn=ocr_fn)

        ocr_fn.assert_not_called()
        assert pages == []

    def test_extract_pages_ocrs_all_empty_pages(self, tmp_path: Path) -> None:
        """extract_pages() — OCR every page when none have native text."""
        empty = _make_pdf(tmp_path / "empty.pdf", ["", ""])
        ocr_fn = MagicMock(side_effect=["OCR-P1", "OCR-P2"])

        with patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", False):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=ocr_fn)

        assert ocr_fn.call_count == 2
        assert pages == [(1, "OCR-P1"), (2, "OCR-P2")]

    # -------------------------------------------------------------------------
    # Merge
    # -------------------------------------------------------------------------

    def test_merge_prepends_existing_text_and_adds_ocr_sections(self) -> None:
        """merge() — keeps MarkItDown text and appends Scanned pages (OCR fallback)."""
        merged = pdf_ocr.merge("Existing text", [(1, "Page one")])

        expected_fragments = [
            "Existing text",
            "## Page 1",
            "### OCR",
            "Page one",
        ]
        for fragment in expected_fragments:
            assert fragment in merged
        assert "```" not in merged
        assert "Scanned pages" not in merged

    def test_merge_returns_only_ocr_when_markitdown_text_empty(self) -> None:
        """merge() — OCR-only output when MarkItDown returned no text."""
        merged = pdf_ocr.merge("", [(1, "Only OCR")])

        assert "Only OCR" in merged
        assert "Scanned pages" not in merged
        assert "```" not in merged

    def test_compose_pdf_markdown_interleaves_native_and_ocr(
        self, tmp_path: Path
    ) -> None:
        mixed = _make_pdf(tmp_path / "mixed.pdf", [LONG_TEXT, ""])
        composed = pdf_ocr.compose_pdf_markdown(
            pdf_path=mixed,
            markitdown_text="unused-short",
            ocr_pages=[(2, "SCANNED")],
            tables=[],
        )
        assert "## Page 1" in composed
        assert LONG_TEXT in composed
        assert "## Page 2" in composed
        assert "### OCR" in composed
        assert "SCANNED" in composed
        assert "```" not in composed

    def test_extract_pages_hybrid_calls_llm_when_tesseract_confidence_low(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — empty Tesseract still calls ocr_fn in hybrid OCR."""
        empty = _make_pdf(tmp_path / "scan.pdf", [""])
        llm_fn = MagicMock(return_value="vision text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.ocr.conversion_config.ocr_confidence_min",
                60.0,
            ),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("", 10.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=llm_fn)

        llm_fn.assert_called_once()
        assert pages == [(1, "vision text")]

    def test_extract_pages_hybrid_skips_llm_when_tesseract_has_text(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — low-confidence nonempty Tesseract does not call ocr_fn."""
        empty = _make_pdf(tmp_path / "scan.pdf", [""])
        llm_fn = MagicMock(return_value="vision text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.ocr.conversion_config.ocr_confidence_min",
                60.0,
            ),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("blurry exam text", 10.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=llm_fn)

        llm_fn.assert_not_called()
        assert pages == [(1, "blurry exam text")]

    def test_extract_pages_stops_remaining_pages_when_cancelled(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — cancel raises so remaining pages are not OCR'd."""
        scan = _make_pdf(tmp_path / "scan.pdf", ["", "", ""])
        cancel = threading.Event()
        calls: list[int] = []

        def ocr_fn(_: bytes) -> str:
            calls.append(1)
            cancel.set()
            return f"OCR-{len(calls)}"

        with patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", False):
            with pytest.raises(ConversionError, match="cancelled"):
                pdf_ocr.extract_pages(scan, ocr_fn=ocr_fn, cancel=cancel)

        assert len(calls) == 1

