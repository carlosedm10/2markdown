"""Test cases for markdown image OCR (src.converter.ocr)."""

from pathlib import Path

from src.converter.ocr import enrich_markdown_images


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
