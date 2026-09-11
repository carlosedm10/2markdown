"""Discover files under an input directory for batch conversion."""

import zipfile
from pathlib import Path

from twomarkdown.config import IWORK_BUNDLE_SUFFIXES, SKIP_DIR_NAMES, conversion_config


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _under_output(path: Path, output_dir: Path) -> bool:
    try:
        path.resolve().relative_to(output_dir.resolve())
        return True
    except ValueError:
        return False


def _inside_iwork_bundle(path: Path) -> bool:
    """True if path is nested inside a .pages / .key / .numbers package."""
    for parent in path.parents:
        if parent.suffix.lower() in IWORK_BUNDLE_SUFFIXES:
            return True
    return False


def _looks_like_iwork_bundle(path: Path) -> bool:
    """Sanity-check directory or zip iWork bundle structure."""
    if path.is_dir():
        markers = ("Metadata", "Index", "Index.zip", "preview.pdf", "preview.jpg")
        return any((path / name).exists() for name in markers)
    if path.is_file() and path.suffix.lower() in IWORK_BUNDLE_SUFFIXES:
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            return any(
                n.startswith("Metadata/")
                or n.startswith("Index/")
                or n == "Index.zip"
                or n == "preview.pdf"
                for n in names
            )
        except (zipfile.BadZipFile, OSError):
            return False
    return False


def _discover_iwork_bundles(
    input_dir: Path,
    output_dir: Path,
    *,
    extensions: frozenset[str],
) -> list[Path]:
    bundles: list[Path] = []
    seen: set[Path] = set()

    for path in input_dir.rglob("*"):
        if _is_hidden(path):
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if _under_output(path, output_dir):
            continue

        suffix = path.suffix.lower()
        if suffix not in IWORK_BUNDLE_SUFFIXES:
            continue
        if suffix not in extensions:
            continue

        candidate = path.resolve()
        if candidate in seen:
            continue

        if path.is_dir() and _looks_like_iwork_bundle(path):
            bundles.append(candidate)
            seen.add(candidate)
        elif path.is_file() and _looks_like_iwork_bundle(path):
            bundles.append(candidate)
            seen.add(candidate)

    return sorted(bundles)


def _effective_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if not conversion_config.sniff_filetype:
        return suffix
    try:
        from twomarkdown.converter.filetype import effective_suffix

        return effective_suffix(path)
    except Exception:
        return suffix


def discover_files(
    input_dir: Path,
    output_dir: Path,
    *,
    include_extensions: frozenset[str] | None = None,
) -> list[Path]:
    """
    Walk input_dir and return a stable sorted list of files to convert.
    """
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    extensions = include_extensions or conversion_config.include_extensions

    files: list[Path] = []
    for path in input_dir.rglob("*"):
        if not path.is_file():
            continue
        if _is_hidden(path):
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if _under_output(path, output_dir):
            continue
        if _inside_iwork_bundle(path):
            continue

        suffix = _effective_suffix(path)
        if suffix not in extensions:
            continue
        if suffix == ".md" and not conversion_config.convert_existing_md:
            continue
        if suffix in IWORK_BUNDLE_SUFFIXES:
            continue

        files.append(path)

    files.extend(_discover_iwork_bundles(input_dir, output_dir, extensions=extensions))
    return sorted(files, key=lambda p: str(p))
