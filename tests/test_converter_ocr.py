"""Test cases for markdown image OCR (src.converter.ocr)."""

from pathlib import Path

from src.converter.ocr import convert_image_file, enrich_markdown_images, is_raster_image


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
        assert "### [OCR generated text]" in enriched
        assert "OCR TEXT" in enriched

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
