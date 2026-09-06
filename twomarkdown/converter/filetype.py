"""Magic-byte file type sniffing for conversion routing."""

from __future__ import annotations

import zipfile
from pathlib import Path

from twomarkdown.config import IWORK_BUNDLE_SUFFIXES, conversion_config

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGICS = (b"\xff\xd8\xff",)
_TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
_ZIP_MAGIC = b"PK"
_EMAIL_HEADERS = ("From:", "Received:", "Subject:")


def _read_prefix(path: Path, size: int = 8192) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def _looks_like_html(prefix: bytes) -> bool:
    try:
        text = prefix.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lowered = text.lstrip().lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")


def _looks_like_email(prefix: bytes) -> bool:
    try:
        text = prefix.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return False
    head = text[:4096]
    return any(header in head for header in _EMAIL_HEADERS)


def _sniff_zip_suffix(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return ".zip"

    if "[Content_Types].xml" in names:
        if "word/document.xml" in names:
            return ".docx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "ppt/presentation.xml" in names:
            return ".pptx"

    if "Index/Metadata.iwa" in names or "Index/Document.iwa" in names:
        suffix = path.suffix.lower()
        if suffix in IWORK_BUNDLE_SUFFIXES:
            return suffix
        return None

    return ".zip"


def sniff_suffix(path: Path) -> str | None:
    """Sniff a file's effective suffix from magic bytes and archive structure."""
    if not path.is_file():
        return path.suffix.lower() or None

    prefix = _read_prefix(path)

    sniffed: str | None = None
    if prefix.startswith(_PDF_MAGIC):
        sniffed = ".pdf"
    elif prefix.startswith(_PNG_MAGIC):
        sniffed = ".png"
    elif any(prefix.startswith(magic) for magic in _JPEG_MAGICS):
        sniffed = ".jpg"
    elif any(prefix.startswith(magic) for magic in _TIFF_MAGICS):
        sniffed = ".tiff"
    elif len(prefix) >= 12 and prefix[4:8] == b"ftyp" and any(
        tag in prefix[8:16] for tag in (b"heic", b"heif", b"mif1", b"msf1")
    ):
        sniffed = ".heic"
    elif prefix.startswith(_ZIP_MAGIC):
        sniffed = _sniff_zip_suffix(path)
    elif _looks_like_html(prefix):
        sniffed = ".html"
    elif _looks_like_email(prefix):
        sniffed = ".eml"

    path_suffix = path.suffix.lower()
    if sniffed and sniffed != path_suffix:
        return sniffed
    return path_suffix or None


def effective_suffix(path: Path) -> str:
    """Return the suffix used for conversion routing."""
    if conversion_config.sniff_filetype:
        sniffed = sniff_suffix(path)
        if sniffed:
            return sniffed
    return path.suffix.lower()
