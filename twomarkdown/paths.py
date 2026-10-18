"""Path helpers for input/output directory layout."""

import os
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


def common_ancestor(paths: list[Path]) -> Path:
    """The deepest directory that contains every entry in `paths`.

    A single entry's own ancestor is itself (a directory) or its parent (a
    file) — the same "root" a lone `path` already resolves to. Two or more
    entries fall back to `os.path.commonpath`, which for a set of distinct
    files/folders always lands on a real directory (never a file), since a
    file can only equal the whole common path when it is the *only* entry.
    """
    resolved = [p.resolve() for p in paths]
    if len(resolved) == 1:
        return resolved[0] if resolved[0].is_dir() else resolved[0].parent
    return Path(os.path.commonpath([str(p) for p in resolved]))


def default_output_dir_for_selection(paths: list[Path]) -> Path:
    """Sibling output folder for a multi-path selection (files and/or
    folders picked together, not one walked directory).

    A single-entry selection defers to `default_output_dir` above (same
    sibling-name rule as always). For two or more entries: when every
    selected entry sits directly inside their common ancestor (a sibling
    group — e.g. three files all in the same folder), the output folder is
    named after that ancestor, next to it — `"<ancestor>_2markdown"` — same
    naming convention as a single folder input. When the selection spans
    entries that are *not* siblings (nested at different depths, or under
    different subtrees entirely), there is no one name that describes the
    selection, so this falls back to a plain `"2markdown"` folder next to
    the first entry, rather than inventing a name from an arbitrary ancestor
    directory the user never actually picked.
    """
    resolved = [p.resolve() for p in paths]
    if len(resolved) == 1:
        return default_output_dir(resolved[0])
    ancestor = common_ancestor(resolved)
    same_parent = all(p.parent == ancestor for p in resolved)
    if same_parent:
        return ancestor.parent / f"{ancestor.name}{OUTPUT_SUFFIX}"
    return resolved[0].parent / "2markdown"
