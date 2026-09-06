"""Deterministic markdown post-processing (mojibake, hyphenation, layout)."""

import re
from collections.abc import Callable

from twomarkdown.config import conversion_config


def _utf8_as_latin1(byte_seq: bytes) -> str:
    return byte_seq.decode("latin-1")


# UTF-8 bytes misread as Latin-1 / Windows-1252
_MOJIBAKE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (_utf8_as_latin1(b"\xe2\x80\x99"), "'"),
    (_utf8_as_latin1(b"\xe2\x80\x98"), "'"),
    (_utf8_as_latin1(b"\xe2\x80\x9c"), '"'),
    (_utf8_as_latin1(b"\xe2\x80\x9d"), '"'),
    (_utf8_as_latin1(b"\xe2\x80\x94"), "—"),
    (_utf8_as_latin1(b"\xe2\x80\x93"), "–"),
    (_utf8_as_latin1(b"\xe2\x80\xa6"), "…"),
    ("Ã©", "é"),
    ("Ã¨", "è"),
    ("Ã ", "à"),
    ("Ã¢", "â"),
    ("Ã®", "î"),
    ("Ã´", "ô"),
    ("Ã¼", "ü"),
    ("Ã±", "ñ"),
    ("Â©", "©"),
    ("Â®", "®"),
    ("Â°", "°"),
    ("Â·", "·"),
    (_utf8_as_latin1(b"\xc2\xa0"), " "),
    ("Â ", " "),
    ("Â", ""),
)

_WINDOWS_QUOTE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\x91", "'"),
    ("\x92", "'"),
    ("\x93", '"'),
    ("\x94", '"'),
)

_DEHYPHENATE_RE = re.compile(r"([A-Za-z])-\n([A-Za-z])")

_PAGE_SECTION_SPLIT_RE = re.compile(r"(?=^## Page\b)", flags=re.MULTILINE)
_PAGE_NUMBER_LINE_RE = re.compile(r"^(?:Page \d+ of \d+|- \d+ -|\d+/\d+)$")


def replace_unicode_mojibake(text: str) -> str:
    """Fix common UTF-8-as-Latin-1 mojibake and Windows quote bytes."""
    for old, new in _MOJIBAKE_REPLACEMENTS:
        text = text.replace(old, new)
    for old, new in _WINDOWS_QUOTE_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def dehyphenate_line_breaks(text: str) -> str:
    """Join words split across a line break: ``docu-\\nment`` → ``document``."""
    return _DEHYPHENATE_RE.sub(r"\1\2", text)


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_heading_line(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_fence_line(line: str) -> bool:
    return line.lstrip().startswith("```")


def group_broken_paragraphs(text: str) -> str:
    """Join single newlines inside prose; preserve paragraph breaks and structure."""
    lines = text.split("\n")
    out: list[str] = []
    prose_buffer: list[str] = []
    in_fence = False

    def flush_prose() -> None:
        if prose_buffer:
            out.append(" ".join(prose_buffer))
            prose_buffer.clear()

    for line in lines:
        if _is_fence_line(line):
            flush_prose()
            in_fence = not in_fence
            out.append(line)
            continue

        if in_fence:
            out.append(line)
            continue

        if line == "":
            flush_prose()
            out.append("")
            continue

        if _is_heading_line(line) or _is_table_line(line):
            flush_prose()
            out.append(line)
            continue

        prose_buffer.append(line.strip())

    flush_prose()
    return "\n".join(out)


def _is_running_header_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return len(stripped) <= 80


def _is_page_number_line(line: str) -> bool:
    return bool(_PAGE_NUMBER_LINE_RE.match(line.strip()))


def strip_repeated_running_headers(text: str) -> str:
    """Remove duplicate short lines across pages and standalone page-number lines."""
    parts = _PAGE_SECTION_SPLIT_RE.split(text)
    if len(parts) <= 1:
        return _drop_page_number_lines(text)

    preamble = parts[0]
    page_parts = parts[1:]

    page_headers: list[str] = []
    page_bodies: list[list[str]] = []

    for part in page_parts:
        lines = part.split("\n", 1)
        page_headers.append(lines[0])
        body = lines[1] if len(lines) > 1 else ""
        page_bodies.append(body.split("\n"))

    line_page_indices: dict[str, set[int]] = {}
    for page_idx, body_lines in enumerate(page_bodies):
        seen_on_page: set[str] = set()
        for line in body_lines:
            if not _is_running_header_candidate(line):
                continue
            stripped = line.strip()
            if stripped in seen_on_page:
                continue
            seen_on_page.add(stripped)
            line_page_indices.setdefault(stripped, set()).add(page_idx)

    repeated_lines = {
        line for line, pages in line_page_indices.items() if len(pages) >= 3
    }

    rebuilt_pages: list[str] = []
    for header, body_lines in zip(page_headers, page_bodies, strict=True):
        filtered: list[str] = []
        for line in body_lines:
            stripped = line.strip()
            if stripped in repeated_lines or _is_page_number_line(line):
                continue
            filtered.append(line)
        body_text = "\n".join(filtered)
        if body_text:
            rebuilt_pages.append(f"{header}\n{body_text}")
        else:
            rebuilt_pages.append(header)

    result = preamble + "".join(rebuilt_pages)
    return result.rstrip("\n") + ("\n" if text.endswith("\n") else "")


def _drop_page_number_lines(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n") if not _is_page_number_line(line)
    )


def clean_markdown(text: str) -> str:
    """Apply deterministic markdown cleaning when enabled in config."""
    if not conversion_config.clean_markdown:
        return text

    steps: tuple[Callable[[str], str], ...] = (
        replace_unicode_mojibake,
        dehyphenate_line_breaks,
        group_broken_paragraphs,
        strip_repeated_running_headers,
    )
    for step in steps:
        text = step(text)
    return text
