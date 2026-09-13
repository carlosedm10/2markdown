"""Convert MATLAB sources to markdown.

Two formats, both worth keeping as code rather than prose:

* ``.m`` is plain text. It becomes one fenced ``matlab`` block, so the code stays
  copy-pasteable and search still finds the identifiers.
* ``.mlx`` (Live Script) is an OOXML zip whose ``matlab/document.xml`` interleaves
  narrative and code, each paragraph tagged ``text`` or ``code``. That structure
  is the point of a Live Script, so it is preserved: prose as prose, code as
  fenced blocks, in document order.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MATLAB_SUFFIXES = frozenset({".m", ".mlx"})
LIVE_SCRIPT_DOCUMENT = "matlab/document.xml"
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class MatlabConversionError(Exception):
    """Raised when a MATLAB source cannot be converted."""


def is_matlab(path: Path) -> bool:
    return path.suffix.lower() in MATLAB_SUFFIXES and path.is_file()


def is_live_script(path: Path) -> bool:
    """True for a .mlx zip that really carries a Live Script document."""
    if path.suffix.lower() != ".mlx" or not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            return LIVE_SCRIPT_DOCUMENT in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _fence(code: str) -> str:
    code = code.strip("\n")
    # A fence must outlast any backticks in the code itself.
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", code)), default=0) + 1)
    return f"{ticks}matlab\n{code}\n{ticks}"


def convert_m_file(path: Path) -> str:
    """Render a plain .m script as a single fenced block."""
    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MatlabConversionError(f"cannot read {path.name}: {exc}") from exc
    if not code.strip():
        raise MatlabConversionError(f"empty MATLAB file: {path.name}")
    return f"## {path.name}\n\n{_fence(code)}\n"


def _paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find(f"{_W_NS}pPr/{_W_NS}pStyle")
    if style is None:
        return "text"
    return (style.get(f"{_W_NS}val") or "text").lower()


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{_W_NS}t"))


def convert_live_script(path: Path) -> str:
    """Render a .mlx Live Script, keeping its narrative/code interleaving."""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read(LIVE_SCRIPT_DOCUMENT)
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        raise MatlabConversionError(f"unreadable Live Script: {exc}") from exc

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise MatlabConversionError(f"invalid Live Script XML: {exc}") from exc

    blocks: list[str] = []
    pending_code: list[str] = []

    def flush_code() -> None:
        if pending_code:
            blocks.append(_fence("\n".join(pending_code)))
            pending_code.clear()

    for paragraph in root.iter(f"{_W_NS}p"):
        text = _paragraph_text(paragraph)
        style = _paragraph_style(paragraph)
        if style == "code":
            # Consecutive code paragraphs are one logical block.
            pending_code.append(text)
            continue
        flush_code()
        if text.strip():
            blocks.append(text.strip())
    flush_code()

    if not blocks:
        raise MatlabConversionError(f"no content in Live Script: {path.name}")
    return f"## {path.name}\n\n" + "\n\n".join(blocks) + "\n"


def convert_matlab(path: Path) -> str:
    """Convert either MATLAB format to markdown."""
    if path.suffix.lower() == ".mlx":
        return convert_live_script(path)
    return convert_m_file(path)
