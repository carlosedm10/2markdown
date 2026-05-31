"""Discover files under an input directory for batch conversion."""

from pathlib import Path

from src.config import SKIP_DIR_NAMES, conversion_config


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _under_output(path: Path, output_dir: Path) -> bool:
    try:
        path.resolve().relative_to(output_dir.resolve())
        return True
    except ValueError:
        return False


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

        if path.suffix.lower() not in extensions:
            continue
        if path.suffix.lower() == ".md" and not conversion_config.convert_existing_md:
            continue

        files.append(path)

    return sorted(files)
