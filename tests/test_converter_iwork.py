"""Apple iWork conversion via preview.pdf (twomarkdown.converter.iwork)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import fitz
import pytest

from twomarkdown.converter import iwork
from twomarkdown.converter.iwork import IWorkConversionError

PREVIEW_TEXT = "Hello iWork preview"


def _write_preview_pdf(path: Path, text: str = PREVIEW_TEXT) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def _make_dir_bundle(root: Path, suffix: str = ".pages") -> Path:
    bundle = root / f"notes{suffix}"
    bundle.mkdir()
    (bundle / "Metadata").mkdir()
    _write_preview_pdf(bundle / "preview.pdf")
    return bundle


def _make_zip_bundle(root: Path, suffix: str = ".pages") -> Path:
    bundle = root / f"notes{suffix}"
    inner = root / "inner"
    inner.mkdir()
    (inner / "Metadata").mkdir()
    _write_preview_pdf(inner / "preview.pdf")
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.write(inner / "Metadata", "Metadata/")
        archive.write(inner / "preview.pdf", "preview.pdf")
    return bundle


class TestIWorkConverter:
    def test_is_iwork_bundle_true_for_pages_directory(self, tmp_path: Path) -> None:
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        assert iwork.is_iwork_bundle(bundle) is True

    def test_convert_bundle_directory_uses_real_preview_pdf(
        self, tmp_path: Path
    ) -> None:
        bundle = _make_dir_bundle(tmp_path)

        def convert_pdf(path: Path) -> str:
            assert path.name == "preview.pdf"
            with fitz.open(path) as doc:
                return doc[0].get_text().strip()

        markdown = iwork.convert_bundle(bundle, convert_pdf=convert_pdf)
        assert PREVIEW_TEXT in markdown

    def test_convert_bundle_zip_uses_real_preview_pdf(self, tmp_path: Path) -> None:
        bundle = _make_zip_bundle(tmp_path, suffix=".key")

        def convert_pdf(path: Path) -> str:
            with fitz.open(path) as doc:
                return doc[0].get_text().strip()

        markdown = iwork.convert_bundle(bundle, convert_pdf=convert_pdf)
        assert PREVIEW_TEXT in markdown

    def test_convert_bundle_raises_when_preview_missing(self, tmp_path: Path) -> None:
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()

        with pytest.raises(IWorkConversionError, match="preview.pdf"):
            iwork.convert_bundle(bundle)

    def test_convert_bundle_raises_when_preview_empty(self, tmp_path: Path) -> None:
        bundle = tmp_path / "doc.numbers"
        bundle.mkdir()
        (bundle / "preview.pdf").write_bytes(b"%PDF-1.4\n")

        with pytest.raises(IWorkConversionError, match="yielded no text"):
            iwork.convert_bundle(bundle, convert_pdf=lambda _p: "")
