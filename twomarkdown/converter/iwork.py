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


# Newer Pages/Keynote bundles ship only a raster preview of the first page.
_PREVIEW_PDF_NAMES = ("preview.pdf", "quicklook/preview.pdf")
_PREVIEW_IMAGE_NAMES = ("preview.jpg", "preview.png", "quicklook/thumbnail.jpg")


def _preview_pdf_in_dir(bundle: Path) -> Path | None:
    for name in _PREVIEW_PDF_NAMES:
        candidate = bundle / name
        if candidate.is_file():
            return candidate
    return None


def _preview_image_in_dir(bundle: Path) -> Path | None:
    for name in _PREVIEW_IMAGE_NAMES:
        candidate = bundle / name
        if candidate.is_file():
            return candidate
    return None


def _extract_preview_image_from_zip(bundle: Path, dest_dir: Path) -> Path | None:
    try:
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
            for wanted in _PREVIEW_IMAGE_NAMES:
                match = next(
                    (n for n in names if n.lower() == wanted or
                     Path(n).name.lower() == Path(wanted).name),
                    None,
                )
                if match is None:
                    continue
                target = (dest_dir / Path(match).name).resolve()
                dest_root = dest_dir.resolve()
                if not str(target).startswith(str(dest_root) + "/"):
                    raise IWorkConversionError(f"zip slip detected: {match}")
                target.write_bytes(zf.read(match))
                return target
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        logger.debug("iWork zip image preview extract failed for %s: %s", bundle, exc)
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


def _convert_image_preview(
    path: Path,
    temp_dir: tempfile.TemporaryDirectory[str] | None,
    ocr_fn: Callable[[bytes], str] | None = None,
) -> str | None:
    """OCR the bundle's raster preview (first page only). Returns None if absent."""
    if path.is_dir():
        image = _preview_image_in_dir(path)
    elif temp_dir is not None:
        image = _extract_preview_image_from_zip(path, Path(temp_dir.name))
    else:
        image = None
    if image is None or not image.is_file():
        return None

    from twomarkdown.converter import ocr

    try:
        # Use the configured engine: these previews are handwritten study notes,
        # where Tesseract alone returns noise.
        text = ocr.ocr_image_bytes(image.read_bytes(), ocr_fn=ocr_fn).strip()
    except Exception as exc:
        logger.debug("iWork preview image OCR failed for %s: %s", path, exc)
        text = ""

    banner = (
        f"## {path.name} — vista previa parcial\n\n"
        "> **Incompleto:** este documento no incluye `preview.pdf`, así que sólo se ha "
        "recuperado la **primera página** desde la vista previa en imagen. "
        "Para convertirlo entero, exporta un PDF desde Pages/Keynote/Numbers.\n"
    )
    if not text:
        return banner
    return f"{banner}\n{text}\n"


def convert_bundle(
    path: Path,
    *,
    convert_pdf: Callable[[Path], str] | None = None,
    ocr_fn: Callable[[bytes], str] | None = None,
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
            # No PDF preview: recover the first page from the raster preview rather
            # than losing the file entirely, and say plainly what is missing.
            partial = _convert_image_preview(path, temp_dir, ocr_fn)
            if partial is not None:
                return partial
            raise IWorkConversionError(
                "no preview.pdf or preview image in bundle; export PDF from Pages, "
                "Keynote, or Numbers"
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
