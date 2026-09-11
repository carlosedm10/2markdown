"""Path helpers for input/output directory layout."""

from pathlib import Path

OUTPUT_SUFFIX = "_2markdown"


def default_output_dir(input_path: Path) -> Path:
    """
    Sibling output folder next to the input path.

    Examples:
        Downloads/folder_to_process -> Downloads/folder_to_process_2markdown
        Downloads/report.pdf        -> Downloads/report_2markdown
    """
    resolved = input_path.resolve()
    if resolved.is_dir():
        return resolved.parent / f"{resolved.name}{OUTPUT_SUFFIX}"
    if resolved.is_file() or resolved.suffix:
        return resolved.parent / f"{resolved.stem}{OUTPUT_SUFFIX}"
    return resolved.parent / f"{resolved.name}{OUTPUT_SUFFIX}"


def normalize_batch_input(input_path: Path) -> tuple[Path, Path, list[Path] | None]:
    """
    Return (batch_root, output_dir, optional fixed file list).

    Directories are walked recursively; a single file converts only that file.
    """
    resolved = input_path.resolve()
    if resolved.is_file():
        return resolved.parent, default_output_dir(resolved), [resolved]
    if resolved.is_dir():
        return resolved, default_output_dir(resolved), None
    if resolved.suffix:
        return resolved.parent, default_output_dir(resolved), [resolved]
    return resolved, default_output_dir(resolved), None
