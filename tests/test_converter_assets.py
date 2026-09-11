"""Tests for PDF asset extraction."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

from twomarkdown.converter import assets


def _make_png_bytes(size: int = 64) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _make_pdf_with_image(path: Path, png_bytes: bytes) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 72 + 100, 72 + 100)
    page.insert_image(rect, stream=png_bytes)
    doc.save(path)
    doc.close()
    return path


class TestPdfAssetExtraction:
    def test_extract_pdf_images_writes_assets_and_returns_page_numbers(
        self, tmp_path: Path
    ) -> None:
        pdf_path = _make_pdf_with_image(tmp_path / "doc.pdf", _make_png_bytes())
        assets_dir = tmp_path / "assets"

        with patch("twomarkdown.converter.assets.conversion_config") as cfg:
            cfg.extract_assets = True
            cfg.min_image_px = 1
            extracted = assets.extract_pdf_images(pdf_path, assets_dir)

        assert len(extracted) == 1
        saved_path, page_number = extracted[0]
        assert page_number == 1
        assert saved_path.is_file()
        assert saved_path.parent == assets_dir
        assert saved_path.name.startswith("doc-p1-1.")

    def test_extract_pdf_images_skips_tiny_images(self, tmp_path: Path) -> None:
        tiny_png = _make_png_bytes(size=2)
        pdf_path = _make_pdf_with_image(tmp_path / "tiny.pdf", tiny_png)
        assets_dir = tmp_path / "assets"

        with patch("twomarkdown.converter.assets.conversion_config") as cfg:
            cfg.extract_assets = True
            cfg.min_image_px = 64
            extracted = assets.extract_pdf_images(pdf_path, assets_dir)

        assert extracted == []
        assert not any(assets_dir.iterdir()) if assets_dir.exists() else True

    def test_extract_pdf_images_returns_empty_when_disabled(
        self, tmp_path: Path
    ) -> None:
        pdf_path = _make_pdf_with_image(tmp_path / "doc.pdf", _make_png_bytes())
        assets_dir = tmp_path / "assets"

        with patch("twomarkdown.converter.assets.conversion_config") as cfg:
            cfg.extract_assets = False
            extracted = assets.extract_pdf_images(pdf_path, assets_dir)

        assert extracted == []

    def test_markdown_asset_index_uses_relative_links(self, tmp_path: Path) -> None:
        output_root = tmp_path / "out"
        assets_dir = output_root / "assets"
        assets_dir.mkdir(parents=True)
        image_path = assets_dir / "doc-p2-1.png"
        image_path.write_bytes(_make_png_bytes())

        md = assets.markdown_asset_index(
            [(image_path, 2)],
            output_root=output_root,
        )

        assert "## Embedded images" in md
        assert "Page 2:" in md
        assert "![doc-p2-1](assets/doc-p2-1.png)" in md
