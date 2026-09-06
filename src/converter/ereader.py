"""Convert native e-reader formats (.epub, .fb2, .mobi, .azw, .azw3) to markdown."""

from __future__ import annotations

import logging
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

EREADER_SUFFIXES = frozenset({".epub", ".mobi", ".azw", ".azw3", ".fb2"})

FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"


class EReaderConversionError(Exception):
    """Raised when e-reader conversion yields no usable content."""


def is_ereader(path: Path) -> bool:
    return path.suffix.lower() in EREADER_SUFFIXES and path.is_file()


def convert_ereader(path: Path) -> str:
    """Dispatch by suffix. Raise EReaderConversionError on empty/unsupported."""
    path = path.resolve()
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return _convert_epub(path)
    if suffix == ".fb2":
        return _convert_fb2(path)
    if suffix in {".mobi", ".azw", ".azw3"}:
        return _convert_mobi(path)
    raise EReaderConversionError(f"unsupported e-reader format: {suffix}")


def _html_to_markdown(html: str) -> str:
    from markdownify import markdownify

    return markdownify(html, heading_style="ATX").strip()


def _extract_html_title(html: str) -> str:
    match = re.search(
        r"<h[12][^>]*>(.*?)</h[12]>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    title = re.sub(r"<[^>]+>", "", match.group(1))
    return title.strip()


def _convert_epub(path: Path) -> str:
    import ebooklib
    from ebooklib import epub

    try:
        book = epub.read_epub(str(path))
    except Exception as exc:
        raise EReaderConversionError(f"epub read failed: {exc}") from exc

    parts: list[str] = []
    titles = book.get_metadata("DC", "title")
    if titles:
        parts.append(f"# {titles[0][0]}")
    creators = book.get_metadata("DC", "creator")
    if creators:
        parts.append(f"**{creators[0][0]}**")

    chapter_num = 0
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        chapter_num += 1
        html = item.get_content().decode("utf-8", errors="replace")
        md = _html_to_markdown(html)
        if not md:
            continue
        title = _extract_html_title(html) or f"Chapter {chapter_num}"
        parts.append(f"## {title}")
        parts.append(md)

    text = "\n\n".join(parts).strip()
    if not text:
        raise EReaderConversionError("epub: no content extracted")
    return text


def _fb2_element_text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _process_fb2_section(section: ET.Element, parts: list[str]) -> None:
    title_el = section.find(f"{FB2_NS}title")
    if title_el is not None:
        title_text = _fb2_element_text(title_el)
        if title_text:
            parts.append(f"## {title_text}")

    for child in section:
        if child.tag == f"{FB2_NS}p":
            para = _fb2_element_text(child)
            if para:
                parts.append(para)
        elif child.tag == f"{FB2_NS}section":
            _process_fb2_section(child, parts)


def _convert_fb2(path: Path) -> str:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise EReaderConversionError(f"fb2 parse failed: {exc}") from exc

    parts: list[str] = []
    book_title = root.find(
        f".//{FB2_NS}description/{FB2_NS}title-info/{FB2_NS}book-title"
    )
    if book_title is not None and book_title.text:
        parts.append(f"# {book_title.text.strip()}")

    for author in root.findall(
        f".//{FB2_NS}description/{FB2_NS}title-info/{FB2_NS}author"
    ):
        name_parts: list[str] = []
        for tag in ("first-name", "last-name", "middle-name"):
            el = author.find(f"{FB2_NS}{tag}")
            if el is not None and el.text:
                name_parts.append(el.text.strip())
        if name_parts:
            parts.append(f"**{' '.join(name_parts)}**")

    body = root.find(f"{FB2_NS}body")
    if body is not None:
        for child in body:
            if child.tag == f"{FB2_NS}section":
                _process_fb2_section(child, parts)
            elif child.tag == f"{FB2_NS}p":
                para = _fb2_element_text(child)
                if para:
                    parts.append(para)

    text = "\n\n".join(parts).strip()
    if not text:
        raise EReaderConversionError("fb2: no content extracted")
    return text


def _convert_mobi(path: Path) -> str:
    try:
        import mobi
    except ImportError as exc:
        raise EReaderConversionError(
            "mobi conversion requires: pip install 'mobi>=0.3.3'"
        ) from exc

    tempdir: str | None = None
    try:
        try:
            tempdir, _extracted = mobi.extract(str(path))
        except Exception as exc:
            raise EReaderConversionError(f"mobi unpack failed: {exc}") from exc

        html_files = sorted(
            p
            for p in Path(tempdir).rglob("*")
            if p.is_file() and p.suffix.lower() in {".html", ".xhtml"}
        )
        parts: list[str] = []
        for i, html_path in enumerate(html_files, 1):
            content = html_path.read_text(encoding="utf-8", errors="replace")
            md = _html_to_markdown(content)
            if not md:
                continue
            parts.append(f"## Chapter {i}")
            parts.append(md)

        text = "\n\n".join(parts).strip()
        if not text:
            raise EReaderConversionError("mobi: no content extracted")
        return text
    finally:
        if tempdir is not None:
            shutil.rmtree(tempdir, ignore_errors=True)

