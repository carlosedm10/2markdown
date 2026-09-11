"""iWork documents arrive as a zip file or as a directory bundle."""

import zipfile
from pathlib import Path

from twomarkdown.converter.iwork import _soffice_to_pdf, is_iwork_bundle


def _make_dir_bundle(path: Path) -> Path:
    """A minimal .pages directory bundle (real layout, not a valid document)."""
    path.mkdir()
    (path / "Index").mkdir()
    (path / "Index" / "Document.iwa").write_bytes(b"\x00\x01fake")
    (path / "preview.jpg").write_bytes(b"\xff\xd8\xfffake")
    return path


class TestBundleForms:
    def test_directory_bundle_is_recognised(self, tmp_path: Path) -> None:
        """is_iwork_bundle() — a directory bundle counts as an iWork document."""
        bundle = _make_dir_bundle(tmp_path / "Doc.pages")
        assert is_iwork_bundle(bundle) is True

    def test_zip_file_is_recognised(self, tmp_path: Path) -> None:
        """is_iwork_bundle() — a single-file zip counts too."""
        path = tmp_path / "Doc.pages"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Index/Document.iwa", "x")
        assert is_iwork_bundle(path) is True

    def test_directory_bundle_is_repacked_not_copied(self, tmp_path: Path) -> None:
        """_soffice_to_pdf() — a directory bundle is staged as a zip.

        copy2 raises IsADirectoryError on a bundle, and LibreOffice silently
        produces nothing when handed a directory, so it must be repacked.
        """
        bundle = _make_dir_bundle(tmp_path / "Doc.pages")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # Conversion itself will fail (the fixture is not a real document); what
        # matters is that staging produced a readable zip rather than raising.
        _soffice_to_pdf(bundle, out_dir)

        staged = out_dir / "src.pages"
        assert staged.is_file(), "directory bundle was not staged as a file"
        assert zipfile.is_zipfile(staged), "staged bundle is not a zip"
        with zipfile.ZipFile(staged) as zf:
            assert "Index/Document.iwa" in zf.namelist()

    def test_missing_source_does_not_raise(self, tmp_path: Path) -> None:
        """_soffice_to_pdf() — an unreadable source soft-fails to None."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        assert _soffice_to_pdf(tmp_path / "nope.pages", out_dir) is None
