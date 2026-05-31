"""Path helpers for input/output directory layout."""

from pathlib import Path

OUTPUT_SUFFIX = "_2markdown"


def default_output_dir(input_dir: Path) -> Path:
    """
    Sibling output folder next to the input directory.

    Example:
        Downloads/folder_to_process -> Downloads/folder_to_process_2markdown
    """
    resolved = input_dir.resolve()
    return resolved.parent / f"{resolved.name}{OUTPUT_SUFFIX}"
