"""Magic-byte file type sniffing for conversion routing."""

from __future__ import annotations

import errno
import logging
import time
import zipfile
from pathlib import Path

from twomarkdown.config import IWORK_BUNDLE_SUFFIXES, conversion_config

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGICS = (b"\xff\xd8\xff",)
_TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
_ZIP_MAGIC = b"PK"
_EMAIL_HEADERS = ("From:", "Received:", "Subject:")
_TRANSIENT_ERRNOS = {
    errno.EAGAIN,
    errno.EDEADLK,
    errno.EBUSY,
    errno.EINTR,
}
_READ_ATTEMPTS = 4


def _retrying_read(path: Path, *, size: int | None) -> bytes:
    """Read a file (prefix or all bytes); retry iCloud/network locks (EDEADLK)."""
    last_exc: OSError | None = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            with path.open("rb") as handle:
                return handle.read() if size is None else handle.read(size)
        except OSError as exc:
            last_exc = exc
            if exc.errno not in _TRANSIENT_ERRNOS:
                raise
            time.sleep(0.05 * (2**attempt))
    assert last_exc is not None
    raise last_exc


def _read_prefix(path: Path, size: int = 8192) -> bytes:
    return _retrying_read(path, size=size)


def read_file_bytes(path: Path) -> bytes:
    """Read an entire file with the same iCloud lock retries as sniffing."""
    return _retrying_read(path, size=None)


def materialize_local_copy(path: Path, *, suffix: str | None = None) -> Path:
    """Copy a source file onto local disk so zip/EPUB parsers can seek it."""
    import tempfile

    data = read_file_bytes(path)
    handle = tempfile.NamedTemporaryFile(
        prefix="twomarkdown-",
        suffix=suffix if suffix is not None else path.suffix,
        delete=False,
    )
    try:
        handle.write(data)
        handle.flush()
    finally:
        handle.close()
    return Path(handle.name)



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

    try:
        prefix = _read_prefix(path)
    except OSError as exc:
        logger.debug("Could not sniff %s: %s", path, exc)
        return path.suffix.lower() or None

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
