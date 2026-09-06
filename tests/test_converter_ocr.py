"""Test cases for markdown image OCR (src.converter.ocr)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.converter.ocr import (
    REMOTE_IMAGE_MAX_BYTES,
    _fetch_remote_image,
    convert_image_file,
    enrich_markdown_images,
    is_raster_image,
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

        result = convert_image_file(img, ocr_fn=fake_ocr, existing_markdown="")

        assert "## slide.png — OCR" in result
        assert "Slide title" in result

    def test_convert_image_file_ocr_when_markitdown_is_image_embed_only(
        self, tmp_path: Path, minimal_png_bytes: bytes
    ) -> None:
        img = tmp_path / "slide.png"
        img.write_bytes(minimal_png_bytes)

        def fake_ocr(_: bytes) -> str:
            return "Slide title"

        result = convert_image_file(
            img, ocr_fn=fake_ocr, existing_markdown="![x](foto.png)"
        )

        assert "## slide.png — OCR" in result
        assert "Slide title" in result

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

        enriched = enrich_markdown_images(markdown, source, ocr_fn=empty_ocr)

        assert enriched == markdown


class TestFetchRemoteImage:
    """Test cases for _fetch_remote_image()."""

    def test_fetch_remote_image_returns_none_when_disabled(self) -> None:
        with patch("src.converter.ocr.conversion_config.fetch_remote_images", False):
            assert _fetch_remote_image("https://example.com/img.png") is None

    @patch("src.converter.ocr.requests.get")
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

        with patch("src.converter.ocr.conversion_config.fetch_remote_images", True):
            assert _fetch_remote_image("https://example.com/big.png") is None

        mock_get.assert_called_once_with(
            "https://example.com/big.png", timeout=20, stream=True
        )

    @patch("src.converter.ocr.requests.get")
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

        with patch("src.converter.ocr.conversion_config.fetch_remote_images", True):
            result = _fetch_remote_image("https://example.com/img.png")

        assert result == image_data
        mock_get.assert_called_once_with(
            "https://example.com/img.png", timeout=20, stream=True
        )


class TestHybridOcr:
    def test_ocr_image_bytes_uses_llm_when_tesseract_confidence_low(self) -> None:
        def llm(_: bytes) -> str:
            return "from-llm"

        with patch("src.converter.ocr.conversion_config.ocr_hybrid", True):
            with patch("src.converter.ocr.conversion_config.ocr_confidence_min", 60.0):
                with patch(
                    "src.converter.ocr.tesseract_ocr_with_confidence",
                    return_value=("weak", 12.0),
                ):
                    with patch(
                        "src.converter.ocr.is_tiny_image",
                        return_value=False,
                    ):
                        assert ocr_image_bytes(b"img", ocr_fn=llm) == "from-llm"

    def test_ocr_image_bytes_keeps_tesseract_when_confidence_high(self) -> None:
        def llm(_: bytes) -> str:
            raise AssertionError("LLM should not run")

        with patch("src.converter.ocr.conversion_config.ocr_hybrid", True):
            with patch("src.converter.ocr.conversion_config.ocr_confidence_min", 60.0):
                with patch(
                    "src.converter.ocr.tesseract_ocr_with_confidence",
                    return_value=("sharp text", 90.0),
                ):
                    with patch(
                        "src.converter.ocr.is_tiny_image",
                        return_value=False,
                    ):
                        assert ocr_image_bytes(b"img", ocr_fn=llm) == "sharp text"
