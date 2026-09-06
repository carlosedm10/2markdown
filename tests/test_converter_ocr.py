"""Test cases for markdown image OCR (twomarkdown.converter.ocr)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from twomarkdown.converter.ocr import (
    REMOTE_IMAGE_MAX_BYTES,
    _fetch_remote_image,
    convert_image_file,
    enrich_markdown_images,
    extract_text_with_tesseract,
    is_raster_image,
    is_tiny_image,
    markdown_has_usable_text,
    ocr_image_bytes,
)


class TestMarkdownHasUsableText:
    """Test cases for markdown_has_usable_text()."""

    def test_markdown_has_usable_text_false_for_empty(self) -> None:
        assert not markdown_has_usable_text("")
        assert not markdown_has_usable_text("   \n\t  ")

    def test_markdown_has_usable_text_false_for_image_only(self) -> None:
        assert not markdown_has_usable_text("![x](foto.png)")
        assert not markdown_has_usable_text("  ![alt](path/to/img.jpg)  ")

    def test_markdown_has_usable_text_true_for_body_text(self) -> None:
        assert markdown_has_usable_text("Already converted")
        assert markdown_has_usable_text("Intro\n\n![x](foto.png)\n\nMore text")


class TestRasterImageDetection:
    """Test cases for is_raster_image()."""

    def test_is_raster_image_true_for_common_suffixes(self) -> None:
        assert is_raster_image(Path("photo.PNG"))
        assert is_raster_image(Path("photo.jpg"))

    def test_is_raster_image_false_for_non_images(self) -> None:
        assert not is_raster_image(Path("doc.pdf"))


class TestStandaloneImageOcr:
    """Test cases for convert_image_file()."""

    def test_convert_image_file_ocr_when_markitdown_empty(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        img = tmp_path / "slide.png"
        img.write_bytes(minimal_png_bytes)

        def fake_ocr(_: bytes) -> str:
            return "Slide title"

        with patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1):
            result = convert_image_file(img, ocr_fn=fake_ocr, existing_markdown="")

        assert "## slide.png — OCR" in result
        assert "Slide title" in result
        assert "```" not in result

    def test_convert_image_file_ocr_when_markitdown_is_image_embed_only(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        img = tmp_path / "slide.png"
        img.write_bytes(minimal_png_bytes)

        def fake_ocr(_: bytes) -> str:
            return "Slide title"

        with patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1):
            result = convert_image_file(
                img, ocr_fn=fake_ocr, existing_markdown="![x](foto.png)"
            )

        assert "## slide.png — OCR" in result
        assert "Slide title" in result
        assert "```" not in result

    def test_convert_image_file_skips_when_markitdown_has_text(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        img = tmp_path / "slide.png"
        img.write_bytes(minimal_png_bytes)

        def fake_ocr(_: bytes) -> str:
            raise AssertionError("OCR should not run when MarkItDown returned text")

        result = convert_image_file(
            img, ocr_fn=fake_ocr, existing_markdown="Already converted"
        )

        assert result == "Already converted"


class TestMarkdownImageOcr:
    """Test cases for enrich_markdown_images()."""

    # -------------------------------------------------------------------------
    # Path resolution
    # -------------------------------------------------------------------------

    def test_enrich_resolves_relative_image_path_and_appends_ocr_block(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        """enrich_markdown_images() — resolves relative image path beside source."""
        source = tmp_path / "doc.md"
        img = tmp_path / "diagram.png"
        img.write_bytes(minimal_png_bytes)
        markdown = "See ![diagram](diagram.png)"
        source.write_text(markdown)

        def fake_ocr(_: bytes) -> str:
            return "OCR TEXT"

        with patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1):
            enriched = enrich_markdown_images(markdown, source, ocr_fn=fake_ocr)

        assert "![diagram](diagram.png)" in enriched
        assert "### [OCR]" in enriched
        assert "OCR TEXT" in enriched
        assert "```" not in enriched

    def test_enrich_leaves_markdown_unchanged_when_ocr_returns_empty(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        """enrich_markdown_images() — unchanged when OCR extractor returns empty."""
        source = tmp_path / "doc.md"
        img = tmp_path / "diagram.png"
        img.write_bytes(minimal_png_bytes)
        markdown = "See ![diagram](diagram.png)"

        def empty_ocr(_: bytes) -> str:
            return ""

        with (
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch("twomarkdown.converter.ocr.conversion_config.describe_figures", False),
        ):
            enriched = enrich_markdown_images(markdown, source, ocr_fn=empty_ocr)

        assert enriched == markdown

    def test_enrich_appends_figure_description_when_ocr_empty(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        source = tmp_path / "doc.md"
        img = tmp_path / "diagram.png"
        img.write_bytes(minimal_png_bytes)
        markdown = "See ![diagram](diagram.png)"

        def empty_ocr(_: bytes) -> str:
            return ""

        with (
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch("twomarkdown.converter.ocr.conversion_config.describe_figures", True),
            patch(
                "twomarkdown.converter.ocr.describe_image_bytes",
                return_value="A bar chart of quarterly revenue.",
            ),
        ):
            enriched = enrich_markdown_images(markdown, source, ocr_fn=empty_ocr)

        assert "### [Figure]" in enriched
        assert "A bar chart of quarterly revenue." in enriched
        assert "```" not in enriched


class TestFetchRemoteImage:
    """Test cases for _fetch_remote_image()."""

    def test_fetch_remote_image_returns_none_when_disabled(self) -> None:
        with patch("twomarkdown.converter.ocr.conversion_config.fetch_remote_images", False):
            assert _fetch_remote_image("https://example.com/img.png") is None

    @patch("twomarkdown.converter.ocr.requests.get")
    def test_fetch_remote_image_returns_none_when_content_length_too_large(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "image/png",
            "Content-Length": str(REMOTE_IMAGE_MAX_BYTES + 1),
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("twomarkdown.converter.ocr.conversion_config.fetch_remote_images", True):
            assert _fetch_remote_image("https://example.com/big.png") is None

        mock_get.assert_called_once_with(
            "https://example.com/big.png", timeout=20, stream=True
        )

    @patch("twomarkdown.converter.ocr.requests.get")
    def test_fetch_remote_image_returns_bytes_under_limit(
        self, mock_get: MagicMock
    ) -> None:
        image_data = b"png-bytes"
        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "image/png",
            "Content-Length": str(len(image_data)),
        }
        mock_resp.iter_content.return_value = [image_data]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("twomarkdown.converter.ocr.conversion_config.fetch_remote_images", True):
            result = _fetch_remote_image("https://example.com/img.png")

        assert result == image_data
        mock_get.assert_called_once_with(
            "https://example.com/img.png", timeout=20, stream=True
        )


class TestTinyImageDetection:
    def test_is_tiny_image_when_both_dimensions_below_threshold(
        self, minimal_png_bytes: bytes
    ) -> None:
        assert is_tiny_image(minimal_png_bytes, min_px=64)

    def test_is_not_tiny_when_either_dimension_meets_threshold(
        self, minimal_png_bytes: bytes
    ) -> None:
        assert not is_tiny_image(minimal_png_bytes, min_px=1)


class TestOcrImageBytesHybrid:
    def test_ocr_image_bytes_skips_tiny_images(self, minimal_png_bytes: bytes) -> None:
        ocr_fn = MagicMock(return_value="LLM text")

        with patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True):
            result = ocr_image_bytes(minimal_png_bytes, ocr_fn=ocr_fn)

        assert result == ""
        ocr_fn.assert_not_called()

    def test_ocr_image_bytes_hybrid_high_confidence_skips_llm(
        self, minimal_png_bytes: bytes
    ) -> None:
        ocr_fn = MagicMock(return_value="LLM text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch("twomarkdown.converter.ocr.conversion_config.ocr_confidence_min", 60.0),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("Tesseract text", 85.0),
            ),
        ):
            result = ocr_image_bytes(minimal_png_bytes, ocr_fn=ocr_fn)

        assert result == "Tesseract text"
        ocr_fn.assert_not_called()

    def test_ocr_image_bytes_hybrid_low_confidence_calls_llm(
        self, minimal_png_bytes: bytes
    ) -> None:
        ocr_fn = MagicMock(return_value="LLM text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch("twomarkdown.converter.ocr.conversion_config.ocr_confidence_min", 60.0),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("", 10.0),
            ),
        ):
            result = ocr_image_bytes(minimal_png_bytes, ocr_fn=ocr_fn)

        assert result == "LLM text"
        ocr_fn.assert_called_once_with(minimal_png_bytes)

    def test_ocr_image_bytes_hybrid_does_not_recurse_on_tesseract_fn(
        self, minimal_png_bytes: bytes
    ) -> None:
        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", True),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
            patch("twomarkdown.converter.ocr.conversion_config.ocr_confidence_min", 60.0),
            patch(
                "twomarkdown.converter.ocr.tesseract_ocr_with_confidence",
                return_value=("Low conf text", 10.0),
            ) as mock_tesseract,
        ):
            result = ocr_image_bytes(
                minimal_png_bytes, ocr_fn=extract_text_with_tesseract
            )

        assert result == "Low conf text"
        mock_tesseract.assert_called_once_with(minimal_png_bytes)

    def test_ocr_image_bytes_non_hybrid_uses_ocr_fn_first(
        self, minimal_png_bytes: bytes
    ) -> None:
        ocr_fn = MagicMock(return_value="LLM text")

        with (
            patch("twomarkdown.converter.ocr.conversion_config.ocr_hybrid", False),
            patch("twomarkdown.converter.ocr.conversion_config.min_image_px", 1),
        ):
            result = ocr_image_bytes(minimal_png_bytes, ocr_fn=ocr_fn)

        assert result == "LLM text"
        ocr_fn.assert_called_once_with(minimal_png_bytes)
