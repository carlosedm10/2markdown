"""Test cases for native e-reader conversion (twomarkdown.converter.ereader)."""

import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from twomarkdown.converter.ereader import (
    EReaderConversionError,
    convert_ereader,
    is_ereader,
)


def _write_minimal_epub(epub_path: Path) -> None:
    """Build a minimal valid-enough EPUB zip in tmp_path."""
    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test EPUB</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:identifier id="uid">test-epub-id</dc:identifier>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>"""
    chapter1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter 1</title></head>
  <body><h1>Intro</h1><p>Hello EPUB</p></body>
</html>"""
    chapter2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter 2</title></head>
  <body><p>Second chapter</p></body>
</html>"""

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/chapter1.xhtml", chapter1)
        zf.writestr("OEBPS/chapter2.xhtml", chapter2)


class TestEReaderConverter:
    """Test cases for e-reader format helpers."""

    def test_convert_ereader_epub_spine_order_and_content(self, tmp_path: Path) -> None:
        """convert_ereader() — EPUB spine chapters convert in order."""
        # Arrange
        epub_path = tmp_path / "book.epub"
        _write_minimal_epub(epub_path)

        # Act
        markdown = convert_ereader(epub_path)

        # Assert
        assert "Hello EPUB" in markdown
        assert "Second chapter" in markdown
        assert markdown.index("Hello EPUB") < markdown.index("Second chapter")

    def test_convert_ereader_epub_rejects_incomplete_icloud_stub(
        self, tmp_path: Path
    ) -> None:
        """convert_ereader() — truncated non-zip bytes get a download hint."""
        epub_path = tmp_path / "book.epub"
        epub_path.write_bytes(b"icloud-stub")

        with pytest.raises(EReaderConversionError, match="not a complete zip"):
            convert_ereader(epub_path)

    def test_convert_ereader_fb2_section_and_paragraph(self, tmp_path: Path) -> None:
        """convert_ereader() — FB2 body sections become markdown."""
        # Arrange
        fb2_path = tmp_path / "book.fb2"
        fb2_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info>
      <book-title>FB2 Title</book-title>
    </title-info>
  </description>
  <body>
    <section>
      <title><p>Section One</p></title>
      <p>FB2 body</p>
    </section>
  </body>
</FictionBook>""",
            encoding="utf-8",
        )

        # Act
        markdown = convert_ereader(fb2_path)

        # Assert
        assert "FB2 body" in markdown
        assert "Section One" in markdown

    def test_is_ereader_true_for_supported_suffixes(self, tmp_path: Path) -> None:
        """is_ereader() — True for supported e-reader files that exist."""
        # Arrange
        for suffix in (".epub", ".fb2", ".mobi", ".azw", ".azw3"):
            path = tmp_path / f"book{suffix}"
            path.write_bytes(b"x")

        # Act / Assert
        for suffix in (".epub", ".fb2", ".mobi", ".azw", ".azw3"):
            assert is_ereader(tmp_path / f"book{suffix}") is True

    def test_is_ereader_false_for_pdf(self, tmp_path: Path) -> None:
        """is_ereader() — False for non e-reader formats like PDF."""
        # Arrange
        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        # Act / Assert
        assert is_ereader(pdf_path) is False

    def test_convert_ereader_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        """convert_ereader() — unknown suffix raises EReaderConversionError."""
        # Arrange
        unknown_path = tmp_path / "notes.txt"
        unknown_path.write_text("plain text", encoding="utf-8")

        # Act / Assert
        with pytest.raises(EReaderConversionError, match="unsupported"):
            convert_ereader(unknown_path)

    def test_convert_ereader_mobi_unpacks_html_to_markdown(
        self, tmp_path: Path
    ) -> None:
        """convert_ereader() — MOBI unpack HTML is converted via markdownify."""
        # Arrange
        mobi_path = tmp_path / "book.mobi"
        mobi_path.write_bytes(b"fake-mobi")
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        html_path = extract_dir / "book.html"
        html_path.write_text("<p>Mobi HTML content</p>", encoding="utf-8")

        def fake_extract(_infile: str) -> tuple[str, str]:
            return str(extract_dir), str(html_path)

        # Act
        with patch("mobi.extract", side_effect=fake_extract):
            markdown = convert_ereader(mobi_path)

        # Assert
        assert "Mobi HTML content" in markdown
