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

    def test_extract_pages_escalates_low_confidence_tesseract_to_llm(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — low-confidence Tesseract is re-OCR'd by vision."""
        empty = _make_pdf(tmp_path / "scan.pdf", [""])
        llm_fn = MagicMock(return_value="vision text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.pdf_ocr.pdf_ocr_config."
                "pdf_ocr_llm_min_confidence",
                75.0,
            ),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("blurry exam text", 10.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=llm_fn)

        llm_fn.assert_called_once()
        assert pages == [(1, "vision text")]

    def test_extract_pages_keeps_confident_tesseract_without_llm(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — confident Tesseract text is kept, ocr_fn unused."""
        empty = _make_pdf(tmp_path / "scan.pdf", [""])
        llm_fn = MagicMock(return_value="vision text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.pdf_ocr.pdf_ocr_config."
                "pdf_ocr_llm_min_confidence",
                75.0,
            ),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("clean printed text", 92.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=llm_fn)

        llm_fn.assert_not_called()
        assert pages == [(1, "clean printed text")]

    def test_extract_pages_falls_back_to_tesseract_when_llm_returns_nothing(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — an empty vision result keeps the Tesseract text."""
        empty = _make_pdf(tmp_path / "scan.pdf", [""])
        llm_fn = MagicMock(return_value="")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.pdf_ocr.pdf_ocr_config."
                "pdf_ocr_llm_min_confidence",
                75.0,
            ),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("blurry exam text", 10.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(empty, ocr_fn=llm_fn)

        llm_fn.assert_called_once()
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



class TestScrambledTextDetection:
    """Equation-object slides extract as long but shredded token soup."""

    def test_detects_scrambled_equation_soup(self) -> None:
        """is_scrambled_text() — loose single characters are not readable text."""
        soup = "Tol x f x x Tol x f Tol x x k k k k k k < + - < < - + + + + ) ( , ) ("
        assert pdf_ocr.is_scrambled_text(soup) is True

    def test_accepts_ordinary_prose(self) -> None:
        """is_scrambled_text() — normal Spanish prose is not flagged."""
        prose = (
            "Determinar un intervalo tal que la funcion tenga distinto signo en "
            "los extremos, y repetir el proceso hasta conseguir un intervalo de "
            "longitud tan pequena como se desee para el metodo de biseccion."
        )
        assert pdf_ocr.is_scrambled_text(prose) is False

    def test_ignores_short_fragments(self) -> None:
        """is_scrambled_text() — too few tokens to judge means no."""
        assert pdf_ocr.is_scrambled_text("a b c d") is False

    def test_compose_drops_scrambled_native_when_ocr_exists(
        self, tmp_path: Path
    ) -> None:
        """compose_pdf_markdown() — OCR replaces a shredded text layer."""
        soup = " ".join(["x", "k", "f", "(", ")", "+", "-", "<"] * 8)
        pdf = _make_pdf(tmp_path / "soup.pdf", [soup])
        composed = pdf_ocr.compose_pdf_markdown(
            pdf_path=pdf,
            markitdown_text="",
            ocr_pages=[(1, "Formula iterativa de Newton")],
            tables=[],
        )
        assert "Formula iterativa de Newton" in composed
        assert "### OCR" in composed

    def test_no_markitdown_duplicate_when_pages_extracted(
        self, tmp_path: Path
    ) -> None:
        """compose_pdf_markdown() — a healthy page tree is not appended twice."""
        # Several pages, so the extracted tree is comfortably over the thin-native
        # threshold that still allows the MarkItDown rescue.
        line = "Metodo de biseccion sobre el intervalo dado y su convergencia."
        pdf = _make_pdf(tmp_path / "ok.pdf", [line] * 5)
        composed = pdf_ocr.compose_pdf_markdown(
            pdf_path=pdf,
            markitdown_text=(line + " ") * 6,
            ocr_pages=[],
            tables=[],
        )
        assert "## Document (MarkItDown)" not in composed

    def test_markitdown_still_rescues_an_empty_page_tree(self, tmp_path: Path) -> None:
        """compose_pdf_markdown() — the fallback survives for unreadable PDFs."""
        pdf = _make_pdf(tmp_path / "empty.pdf", [""])
        composed = pdf_ocr.compose_pdf_markdown(
            pdf_path=pdf,
            markitdown_text="Recovered by MarkItDown " * 20,
            ocr_pages=[],
            tables=[],
        )
        assert "Recovered by MarkItDown" in composed

    def test_scrambled_page_goes_straight_to_the_vision_model(
        self, tmp_path: Path
    ) -> None:
        """extract_pages() — a shredded page skips Tesseract's confidence gate."""
        soup = " ".join(["x", "k", "f", "(", ")", "+", "-", "<"] * 8)
        pdf = _make_pdf(tmp_path / "soup.pdf", [soup])
        llm_fn = MagicMock(return_value="Tasas de convergencia")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("confident prose, shredded maths", 95.0),
            ),
        ):
            pages = pdf_ocr.extract_pages(pdf, ocr_fn=llm_fn)

        llm_fn.assert_called_once()
        assert pages == [(1, "Tasas de convergencia")]


class TestFragmentedTextRepair:
    """Some renderers position text per character: "Eval uaci ón de Met odol ogí as"."""

    @staticmethod
    def _words(pieces: list[tuple[str, float, float]]) -> list:
        """Build PyMuPDF "words" tuples: (x0, y0, x1, y1, text, block, line, n)."""
        return [
            (x0, 0.0, x1, 10.0, text, 0, 0, i)
            for i, (text, x0, x1) in enumerate(pieces)
        ]

    def test_rejoins_fragments_and_keeps_real_spaces(self) -> None:
        """rejoin_words() — narrow gaps close, wide gaps stay as spaces."""
        # Fragments sit 2pt apart; real word breaks are 7pt. Repeated so the
        # sample is large enough for the detector to judge the distribution.
        pieces: list[tuple[str, float, float]] = []
        x = 0.0
        for _ in range(3):
            for group in (["Eval", "uaci", "ón"], ["de"], ["Met", "odol", "ogí", "as"]):
                for token in group:
                    pieces.append((token, x, x + 5.0 * len(token)))
                    x += 5.0 * len(token) + 2.0
                x += 5.0  # wider gap between real words
        words = self._words(pieces)
        out = pdf_ocr.rejoin_words(words)
        assert out is not None
        assert "Evaluación" in out
        assert "Metodologías" in out
        assert "Evaluaciónde" not in out

    def test_leaves_evenly_spaced_text_alone(self) -> None:
        """rejoin_words() — a single gap population is ordinary spacing."""
        tokens = (
            "uno dos tres cuatro cinco seis siete ocho nueve diez once doce"
        ).split()
        words = self._words(
            [(t, i * 30.0, i * 30.0 + 22.0) for i, t in enumerate(tokens)]
        )
        assert pdf_ocr.rejoin_words(words) is None

    def test_never_glues_two_real_words(self) -> None:
        """rejoin_words() — long tokens are words, never fragments.

        Dense maths pages tripped the detector and produced
        "Operacionesconmatrices"; only short pieces may be joined.
        """
        pieces = []
        x = 0.0
        for _ in range(4):
            for token in ("Operaciones", "con", "matrices"):
                pieces.append((token, x, x + 5.0 * len(token)))
                x += 5.0 * len(token) + 2.0
            x += 5.0
        words = self._words(pieces)
        out = pdf_ocr.rejoin_words(words) or ""
        assert "Operacionescon" not in out
        assert "conmatrices" not in out

    def test_too_few_words_is_left_alone(self) -> None:
        """rejoin_words() — not enough evidence to judge means no repair."""
        assert pdf_ocr.rejoin_words(self._words([("a", 0.0, 5.0)])) is None

    def test_short_page_is_left_alone(self, tmp_path: Path) -> None:
        """repair_fragmented_page_text() — a tiny page is never repaired."""
        pdf = _make_pdf(tmp_path / "tiny.pdf", ["hola"])
        page = fitz.open(pdf)[0]
        assert pdf_ocr.repair_fragmented_page_text(page) is None
