"""Tests for file type sniffing (twomarkdown.converter.filetype)."""

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from twomarkdown.converter.filetype import (
    _read_prefix,
    effective_suffix,
    sniff_suffix,
)
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

    def test_sniff_suffix_falls_back_to_path_when_icloud_lock_persists(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "book.epub"
        path.write_bytes(b"PK\x03\x04")

        with patch(
            "twomarkdown.converter.filetype._read_prefix",
            side_effect=OSError(errno.EDEADLK, "Resource deadlock avoided"),
        ):
            assert sniff_suffix(path) == ".epub"

    def test_read_prefix_retries_transient_deadlock(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.4 content")
        attempts = {"n": 0}
        original_open = Path.open

        def flaky(self: Path, *args: object, **kwargs: object):
            if self.resolve() == path.resolve() and attempts["n"] < 2:
                attempts["n"] += 1
                raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return original_open(self, *args, **kwargs)

        with patch.object(Path, "open", flaky), patch(
            "twomarkdown.converter.filetype.time.sleep", return_value=None
        ):
            assert _read_prefix(path).startswith(b"%PDF")
        assert attempts["n"] == 2
