"""Tests for zip ingestion (src.converter.zip_ingest)."""

import zipfile
from pathlib import Path

import pytest

from src.converter.zip_ingest import extract_zip, is_explodable_zip


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class TestZipIngest:
    def test_is_explodable_zip_true_for_plain_zip(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bundle.zip"
        _write_zip(zip_path, {"readme.txt": b"hello"})

        assert is_explodable_zip(zip_path) is True

    def test_is_explodable_zip_false_for_epub(self, tmp_path: Path) -> None:
        epub = tmp_path / "book.epub"
        _write_zip(
            epub,
            {
                "mimetype": b"application/epub+zip",
                "META-INF/container.xml": b"<container/>",
            },
        )

        assert is_explodable_zip(epub) is False

    def test_is_explodable_zip_false_for_pdf_without_sniffing_as_zip(
        self, tmp_path: Path
    ) -> None:
        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 content")

        assert is_explodable_zip(pdf_path) is False

    def test_is_explodable_zip_false_for_docx(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "file.docx"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("[Content_Types].xml", b"<Types/>")
            archive.writestr("word/document.xml", b"<w:document/>")

        assert is_explodable_zip(zip_path) is False

    def test_extract_zip_returns_files(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "bundle.zip"
        _write_zip(
            zip_path,
            {
                "docs/readme.txt": b"hello",
                "docs/nested/data.json": b"{}",
            },
        )
        dest = tmp_path / "out"

        extracted = extract_zip(zip_path, dest)

        assert len(extracted) == 2
        assert (dest / "docs/readme.txt").read_text() == "hello"
        assert (dest / "docs/nested/data.json").read_text() == "{}"

    def test_extract_zip_rejects_zip_slip(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "evil.zip"
        _write_zip(zip_path, {"../escape.txt": b"nope"})
        dest = tmp_path / "out"

        with pytest.raises(ValueError, match="zip slip"):
            extract_zip(zip_path, dest)
