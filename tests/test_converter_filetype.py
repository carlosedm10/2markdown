"""Tests for file type sniffing (twomarkdown.converter.filetype)."""

from pathlib import Path

import pytest

from twomarkdown.converter.filetype import effective_suffix, sniff_suffix
from tests.conftest import MINIMAL_PNG_BYTES


class TestFiletypeSniffing:
    def test_sniff_suffix_pdf_magic(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "document.txt"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")

        assert sniff_suffix(pdf_path) == ".pdf"

    def test_sniff_suffix_png_magic(self, tmp_path: Path) -> None:
        png_path = tmp_path / "image.dat"
        png_path.write_bytes(MINIMAL_PNG_BYTES)

        assert sniff_suffix(png_path) == ".png"

    def test_sniff_suffix_respects_path_when_sniff_matches(
        self, tmp_path: Path
    ) -> None:
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 content")

        assert sniff_suffix(pdf_path) == ".pdf"

    def test_effective_suffix_uses_sniff_when_enabled(self, tmp_path: Path) -> None:
        png_path = tmp_path / "blob.bin"
        png_path.write_bytes(MINIMAL_PNG_BYTES)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "twomarkdown.converter.filetype.conversion_config.sniff_filetype",
                True,
            )
            assert effective_suffix(png_path) == ".png"

    def test_effective_suffix_ignores_sniff_when_disabled(self, tmp_path: Path) -> None:
        png_path = tmp_path / "blob.bin"
        png_path.write_bytes(MINIMAL_PNG_BYTES)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "twomarkdown.converter.filetype.conversion_config.sniff_filetype",
                False,
            )
            assert effective_suffix(png_path) == ".bin"

    def test_sniff_suffix_tiff_magic(self, tmp_path: Path) -> None:
        tiff_path = tmp_path / "scan.bin"
        tiff_path.write_bytes(b"II*\x00" + b"\x00" * 8)
        assert sniff_suffix(tiff_path) == ".tiff"
