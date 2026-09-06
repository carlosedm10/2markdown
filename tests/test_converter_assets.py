"""Tests for PDF asset extraction and iWork embedded image helpers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

from src.converter import assets, iwork
from tests.conftest import MINIMAL_PNG_BYTES


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

        with patch("src.converter.assets.conversion_config") as cfg:
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

        with patch("src.converter.assets.conversion_config") as cfg:
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

        with patch("src.converter.assets.conversion_config") as cfg:
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


class TestIWorkBundleImages:
    def test_iter_bundle_images_finds_data_raster_files(self, tmp_path: Path) -> None:
        bundle = tmp_path / "Foo.pages"
        data_dir = bundle / "Data"
        data_dir.mkdir(parents=True)
        (bundle / "Metadata").mkdir()
        image_path = data_dir / "x.png"
        image_path.write_bytes(MINIMAL_PNG_BYTES)
        (data_dir / "notes.txt").write_text("not an image")

        found = iwork._iter_bundle_images(bundle)

        assert found == [image_path]

    def test_embed_images_markdown_lists_filenames(self, tmp_path: Path) -> None:
        bundle = tmp_path / "Foo.pages"
        data_dir = bundle / "Data"
        data_dir.mkdir(parents=True)
        (bundle / "Metadata").mkdir()
        (data_dir / "x.png").write_bytes(MINIMAL_PNG_BYTES)

        md = iwork.embed_images_markdown(bundle)

        assert "## Embedded images" in md
        assert "- x.png" in md

    def test_embed_images_markdown_includes_ocr_text(self, tmp_path: Path) -> None:
        bundle = tmp_path / "Foo.pages"
        data_dir = bundle / "Data"
        data_dir.mkdir(parents=True)
        (data_dir / "x.png").write_bytes(MINIMAL_PNG_BYTES)

        md = iwork.embed_images_markdown(bundle, ocr_fn=lambda _: "hello ocr")

        assert "- x.png" in md
        assert "### [OCR] hello ocr" in md

    def test_convert_bundle_warns_once_for_app_export(self, tmp_path: Path) -> None:
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()

        with (
            patch("src.converter.iwork.iwork_config") as cfg,
            patch(
                "src.converter.iwork._extract_iwa_text_from_bundle",
                return_value="body",
            ),
            patch("src.converter.iwork.logger") as mock_logger,
        ):
            cfg.iwork_backend = "native"
            cfg.iwork_use_app_export = True
            cfg.iwork_enabled = True
            iwork._app_export_warned = False
            iwork.convert_bundle(bundle)
            iwork.convert_bundle(bundle)

        warning_messages = [
            str(call.args[0]) for call in mock_logger.warning.call_args_list
        ]
        assert (
            sum(
                "IWORK_USE_APP_EXPORT is not supported in Docker" in msg
                for msg in warning_messages
            )
            == 1
        )
