"""Safe extraction of generic .zip archives for batch ingestion."""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.converter.filetype import sniff_suffix

_OFFICE_ZIP_SUFFIXES = frozenset({".docx", ".xlsx", ".pptx"})
_IWORK_ZIP_SUFFIXES = frozenset({".pages", ".key", ".numbers"})


def is_explodable_zip(path: Path) -> bool:
    """Return True for plain .zip archives that are not Office or iWork bundles."""
    if path.suffix.lower() != ".zip" and sniff_suffix(path) != ".zip":
        return False

    effective = sniff_suffix(path)
    if effective in _OFFICE_ZIP_SUFFIXES | _IWORK_ZIP_SUFFIXES:
        return False

    try:
        with zipfile.ZipFile(path) as archive:
            archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False

    return effective == ".zip"


def _safe_extract_path(name: str, dest_dir: Path) -> Path:
    target = (dest_dir / name).resolve()
    dest_root = dest_dir.resolve()
    if not str(target).startswith(str(dest_root) + "/") and target != dest_root:
        raise ValueError(f"zip slip detected: {name}")
    return target


def extract_zip(path: Path, dest_dir: Path) -> list[Path]:
    """Extract a zip archive safely and return paths to extracted files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = _safe_extract_path(info.filename, dest_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as dest:
                dest.write(source.read())
            extracted.append(target)

    return extracted
