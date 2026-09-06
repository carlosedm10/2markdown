"""Convert Apple iWork bundles (.pages, .key, .numbers) via bundled preview.pdf."""

from __future__ import annotations

import logging
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from twomarkdown.config import IWORK_BUNDLE_SUFFIXES, iwork_config

logger = logging.getLogger(__name__)


class IWorkConversionError(Exception):
    """Raised when an iWork bundle has no usable preview.pdf."""


def is_iwork_bundle(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in IWORK_BUNDLE_SUFFIXES:
        return False
    if not iwork_config.iwork_enabled:
        return False
    return path.is_dir() or path.is_file()


def _preview_pdf_in_dir(bundle: Path) -> Path | None:
    candidate = bundle / "preview.pdf"
    if candidate.is_file():
        return candidate
    return None


def _extract_preview_from_zip(bundle: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
            match = next(
                (name for name in names if Path(name).name.lower() == "preview.pdf"),
                None,
            )
            if match is None:
                return None
            target = (dest_dir / "preview.pdf").resolve()
            dest_root = dest_dir.resolve()
            if not str(target).startswith(str(dest_root) + "/") and target != dest_root:
                raise IWorkConversionError(f"zip slip detected: {match}")
            target.write_bytes(zf.read(match))
            return target
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        logger.debug("iWork zip preview extract failed for %s: %s", bundle, exc)
        return None


def convert_bundle(
    path: Path,
    *,
    convert_pdf: Callable[[Path], str] | None = None,
) -> str:
    """Convert an iWork bundle by running the PDF pipeline on preview.pdf."""
    path = path.resolve()
    suffix = path.suffix.lower()
    if suffix not in IWORK_BUNDLE_SUFFIXES:
        raise IWorkConversionError(f"unsupported iWork type: {suffix}")

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    preview: Path | None = None
    try:
        if path.is_dir():
            preview = _preview_pdf_in_dir(path)
        elif path.is_file():
            temp_dir = tempfile.TemporaryDirectory()
            preview = _extract_preview_from_zip(path, Path(temp_dir.name))

        if preview is None or not preview.is_file():
            raise IWorkConversionError(
                "no preview.pdf in bundle; export PDF from Pages, Keynote, or Numbers"
            )

        if convert_pdf is not None:
            text = convert_pdf(preview).strip()
        else:
            from twomarkdown.converter import markitdown_converter

            text = markitdown_converter.convert_file(preview).strip()

        if not text:
            raise IWorkConversionError(
                f"preview.pdf yielded no text: {path.name}"
            )
        return text
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
