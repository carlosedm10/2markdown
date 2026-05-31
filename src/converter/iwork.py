"""Convert Apple iWork bundles (.pages, .key, .numbers) to markdown."""

from __future__ import annotations

import logging
import re
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.config import IWORK_BUNDLE_SUFFIXES, iwork_config

logger = logging.getLogger(__name__)


class IWorkConversionError(Exception):
    """Raised when iWork conversion yields no usable content."""


def is_iwork_bundle(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in IWORK_BUNDLE_SUFFIXES:
        return False
    if not iwork_config.iwork_enabled:
        return False
    return path.is_dir() or path.is_file()


def convert_bundle(
    path: Path,
    *,
    convert_pdf: Callable[[Path], str] | None = None,
) -> str:
    """Convert an iWork bundle to markdown text."""
    path = path.resolve()
    if iwork_config.iwork_backend == "kreuzberg":
        return _convert_with_kreuzberg(path)

    suffix = path.suffix.lower()
    if suffix == ".numbers":
        return _convert_numbers(path)
    if suffix == ".key":
        return _convert_keynote(path)
    if suffix == ".pages":
        return _convert_pages(path, convert_pdf=convert_pdf)
    raise IWorkConversionError(f"unsupported iWork type: {suffix}")


def _convert_with_kreuzberg(path: Path) -> str:
    try:
        from kreuzberg import extract_file_sync
    except ImportError as exc:
        raise IWorkConversionError(
            "iWork backend 'kreuzberg' requires: "
            "pip install 'twomarkdown[iwork-kreuzberg]'"
        ) from exc

    result = extract_file_sync(str(path))
    text = (result.content or "").strip()
    if not text:
        raise IWorkConversionError("kreuzberg returned empty content")
    return text


def _cell_to_str(cell: Any) -> str:
    if cell is None:
        return ""
    value = getattr(cell, "value", cell)
    if value is None:
        return ""
    return str(value).replace("\n", " ").strip()


def _rows_to_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines: list[str] = []
    for i, row in enumerate(normalized):
        line = "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"
        lines.append(line)
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines)


def _convert_numbers(path: Path) -> str:
    from numbers_parser import Document

    doc = Document(str(path))
    sections: list[str] = []

    for sheet in doc.sheets:
        sections.append(f"## {sheet.name}")
        tables = sheet.tables
        if not tables:
            sections.append("")
            continue
        for table in tables:
            name = getattr(table, "name", None)
            if name:
                sections.append(f"### {name}")
            rows = table.rows(values_only=True)
            if rows:
                str_rows = [[_cell_to_str(c) for c in row] for row in rows]
                sections.append(_rows_to_markdown_table(str_rows))
            sections.append("")

    text = "\n".join(sections).strip()
    if not text:
        raise IWorkConversionError("numbers: no table data extracted")
    return text


def _collect_text_from_obj(obj: Any, texts: list[str]) -> None:
    if isinstance(obj, dict):
        if "text" in obj:
            raw = obj["text"]
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and item.strip():
                        texts.append(item.strip())
            elif isinstance(raw, str) and raw.strip():
                texts.append(raw.strip())
        for value in obj.values():
            _collect_text_from_obj(value, texts)
    elif isinstance(obj, list):
        for item in obj:
            _collect_text_from_obj(item, texts)


def _extract_iwa_texts_from_reader(
    path: Path,
    *,
    progress: bool = False,
) -> list[tuple[str, list[str]]]:
    from keynote_parser.codec import IWAFile
    from keynote_parser.file_utils import directory_reader, zip_file_reader

    sections: list[tuple[str, list[str]]] = []
    if path.is_file():
        reader = zip_file_reader(str(path), progress=progress)
    else:
        reader = directory_reader(str(path), progress=progress)

    for filename, handle in reader:
        if ".iwa" not in filename.lower():
            continue
        try:
            data = handle.read()
            iwa = IWAFile.from_buffer(data, filename)
            texts: list[str] = []
            _collect_text_from_obj(iwa.to_dict(), texts)
            if texts:
                sections.append((filename, texts))
        except Exception as exc:
            logger.debug("Skipping IWA %s: %s", filename, exc)
    return sections


def _sections_to_markdown(
    sections: list[tuple[str, list[str]]],
    *,
    heading_prefix: str,
) -> str:
    parts: list[str] = []
    slide_re = re.compile(r"Slide[-_]?(\d+)", re.IGNORECASE)
    for i, (filename, texts) in enumerate(sections, 1):
        match = slide_re.search(filename)
        label = match.group(1) if match else str(i)
        parts.append(f"## {heading_prefix} {label}")
        parts.extend(texts)
        parts.append("")
    return "\n".join(parts).strip()


def _convert_keynote(path: Path) -> str:
    sections = _extract_iwa_texts_from_reader(path, progress=False)
    if not sections:
        raise IWorkConversionError("keynote: no text extracted from IWA archives")
    return _sections_to_markdown(sections, heading_prefix="Slide")


def _iter_iwa_sources(bundle: Path) -> list[tuple[str, bytes]]:
    """Yield (name, raw_bytes) for .iwa entries in bundle Index."""
    sources: list[tuple[str, bytes]] = []

    def add_from_zip(zf: zipfile.ZipFile, prefix: str = "") -> None:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            if ".iwa" not in name.lower():
                continue
            try:
                sources.append((prefix + name, zf.read(name)))
            except KeyError:
                continue

    if bundle.is_dir():
        index_zip = bundle / "Index.zip"
        if index_zip.is_file():
            with zipfile.ZipFile(index_zip) as zf:
                add_from_zip(zf, "Index.zip/")
        index_dir = bundle / "Index"
        if index_dir.is_dir():
            for iwa_path in sorted(index_dir.rglob("*.iwa")):
                rel = iwa_path.relative_to(bundle).as_posix()
                sources.append((rel, iwa_path.read_bytes()))
    elif bundle.is_file():
        with zipfile.ZipFile(bundle) as zf:
            add_from_zip(zf)
            if "Index.zip" in zf.namelist():
                import io

                with zipfile.ZipFile(io.BytesIO(zf.read("Index.zip"))) as inner:
                    add_from_zip(inner, "Index.zip/")

    return sources


def _extract_iwa_text_from_bundle(bundle: Path) -> str:
    from keynote_parser.codec import IWAFile

    sections: list[tuple[str, list[str]]] = []
    for filename, data in _iter_iwa_sources(bundle):
        try:
            iwa = IWAFile.from_buffer(data, filename)
            texts: list[str] = []
            _collect_text_from_obj(iwa.to_dict(), texts)
            if texts:
                sections.append((filename, texts))
        except Exception as exc:
            logger.debug("Skipping IWA %s: %s", filename, exc)

    if not sections:
        return ""
    return _sections_to_markdown(sections, heading_prefix="Section")


def _convert_pages(
    path: Path,
    *,
    convert_pdf: Callable[[Path], str] | None = None,
) -> str:
    preview: Path | None = None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if path.is_dir():
        candidate = path / "preview.pdf"
        if candidate.is_file():
            preview = candidate
    elif path.is_file():
        try:
            with zipfile.ZipFile(path) as zf:
                if "preview.pdf" in zf.namelist():
                    temp_dir = tempfile.TemporaryDirectory()
                    preview = Path(temp_dir.name) / "preview.pdf"
                    preview.write_bytes(zf.read("preview.pdf"))
        except (zipfile.BadZipFile, OSError):
            preview = None

    try:
        if preview is not None and preview.is_file():
            if convert_pdf is not None:
                text = convert_pdf(preview).strip()
            else:
                from src.converter import markitdown_converter

                text = markitdown_converter.convert_file(preview).strip()
            if text:
                return text
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    if iwork_config.iwork_use_app_export and sys.platform == "darwin":
        exported = _try_macos_app_export(path)
        if exported:
            return exported

    iwa_text = _extract_iwa_text_from_bundle(path)
    if iwa_text:
        return iwa_text

    raise IWorkConversionError(
        "pages: no preview.pdf and IWA text extraction yielded nothing"
    )


def _try_macos_app_export(path: Path) -> str:
    """Optional macOS export via Pages.app (off by default)."""
    import subprocess

    suffix = path.suffix.lower()
    app_map = {".pages": "Pages", ".key": "Keynote", ".numbers": "Numbers"}
    app_name = app_map.get(suffix)
    if not app_name:
        return ""

    export_map = {".pages": "PDF", ".key": "PDF", ".numbers": "Microsoft Excel"}
    export_format = export_map[suffix]

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / f"export{'.pdf' if 'PDF' in export_format else '.xlsx'}"
        script = f'''
        tell application "{app_name}"
            set docRef to open POSIX file "{path}"
            export docRef to POSIX file "{out_path}" as {export_format}
            close docRef saving no
        end tell
        '''
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
        ):
            return ""

        if not out_path.is_file():
            return ""

        from src.converter import markitdown_converter

        return markitdown_converter.convert_file(out_path).strip()
