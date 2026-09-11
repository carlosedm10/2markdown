"""Deterministic markdown post-processing (mojibake, hyphenation, layout)."""

import re
import unicodedata
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

# LaTeX/Computer Modern PDFs emit ligatures and big math delimiters as C0 control
# codes: the font's ToUnicode CMap never maps them. Derived from this corpus by
# reading the surrounding words ("\x1dujo" -> "flujo", "L−1\x14 1/s5 \x15" -> "[...]").
_PDF_CONTROL_CHARS: tuple[tuple[str, str], ...] = (
    # Ligatures — OT1 slots 0x0B-0x0F and the shifted 0x1B-0x1E variant.
    ("\x0b", "ff"),
    ("\x0c", "fi"),
    ("\x0d", "fl"),
    ("\x0e", "ffi"),
    ("\x0f", "ffl"),
    ("\x1c", "fi"),
    ("\x1d", "fl"),
    ("\x1e", "ffi"),
    # Extensible math delimiters, in matched pairs.
    ("\x10", "("),
    ("\x11", ")"),
    ("\x12", "("),
    ("\x13", ")"),
    ("\x14", "["),
    ("\x15", "]"),
    ("\x00", "{"),
    ("\x01", "}"),
    ("\x08", "{"),
    ("\x1a", "{"),
    ("\x1b", "}"),
    ("\x03", "∫"),
)

# Any C0 control code left after the mapping above is an unmapped glyph, not text.
# It must not reach the .md file: a stray NUL makes git treat the file as binary.
_RESIDUAL_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Spacing accents that LaTeX PDFs emit next to the letter instead of composed onto
# it: "M´odulo" -> "Módulo", "tama˜no" -> "tamaño".
# The ASCII backtick is deliberately absent: it opens code fences in Markdown, and
# treating it as a grave accent turns "```json" into "``j̀son".
_SPACING_TO_COMBINING: dict[str, str] = {
    "´": "́",  # acute
    "˜": "̃",  # tilde
    "¨": "̈",  # diaeresis
    "ˆ": "̂",  # circumflex
    "ˋ": "̀",  # modifier-letter grave (U+02CB)
    "ˇ": "̌",  # caron
    "˚": "̊",  # ring
}
_ACCENT_CLASS = "".join(_SPACING_TO_COMBINING)
_ACCENT_BEFORE_RE = re.compile(f"([{_ACCENT_CLASS}])([A-Za-z])")
_ACCENT_AFTER_RE = re.compile(f"([A-Za-z])([{_ACCENT_CLASS}])")

_LIGATURE_FORMS: tuple[tuple[str, str], ...] = (
    ("ﬀ", "ff"),
    ("ﬁ", "fi"),
    ("ﬂ", "fl"),
    ("ﬃ", "ffi"),
    ("ﬄ", "ffl"),
    ("ﬅ", "st"),
    ("ﬆ", "st"),
)

# Vision models emit LaTeX delimiters and sectioning commands whatever the prompt
# says. Markdown renderers (Obsidian, GitHub, MathJax-in-Markdown) understand
# $...$ / $$...$$ and #-headings, and show "Undefined control sequence: \[" for
# the rest, so normalise deterministically instead of relying on the prompt.
_LATEX_DISPLAY_RE = re.compile(r"\\\[(.+?)\\\]", flags=re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"\\\((.+?)\\\)", flags=re.DOTALL)
_LATEX_ENV_RE = re.compile(
    r"\\begin\{(equation|equation\*|align|align\*|displaymath|gather|gather\*)\}"
    r"(.+?)"
    r"\\end\{\1\}",
    flags=re.DOTALL,
)
_LATEX_SECTION_RE = re.compile(
    r"^[ \t]*\\(sub)?(sub)?section\*?\{([^}]*)\}[ \t]*$", flags=re.MULTILINE
)
_LATEX_TEXTBF_RE = re.compile(r"\\textbf\{([^}]*)\}")
_LATEX_TEXTIT_RE = re.compile(r"\\textit\{([^}]*)\}")
_FENCE_SPLIT_RE = re.compile(r"(```.*?```)", flags=re.DOTALL)

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


def repair_pdf_text_artifacts(text: str) -> str:
    """Recover ligatures, math delimiters and accents mangled by PDF extraction.

    LaTeX-produced PDFs hand PyMuPDF control codes and free-standing accent glyphs
    instead of real characters, so "Control de flujo" arrives as "Control de \\x1dujo"
    and "Módulo" as "M´odulo". Both are repaired here; anything still unmapped is
    dropped so no control byte reaches the markdown.
    """
    for old, new in _PDF_CONTROL_CHARS:
        text = text.replace(old, new)
    text = _RESIDUAL_CONTROL_RE.sub("", text)

    for old, new in _LIGATURE_FORMS:
        text = text.replace(old, new)

    def _before(match: re.Match) -> str:
        accent, letter = match.group(1), match.group(2)
        return unicodedata.normalize("NFC", letter + _SPACING_TO_COMBINING[accent])

    def _after(match: re.Match) -> str:
        letter, accent = match.group(1), match.group(2)
        return unicodedata.normalize("NFC", letter + _SPACING_TO_COMBINING[accent])

    text = _ACCENT_BEFORE_RE.sub(_before, text)
    text = _ACCENT_AFTER_RE.sub(_after, text)
    return unicodedata.normalize("NFC", text)


def _normalize_latex_segment(text: str) -> str:
    def _env(match: re.Match) -> str:
        return f"\n$$\n{match.group(2).strip()}\n$$\n"

    def _section(match: re.Match) -> str:
        depth = 1 + (1 if match.group(1) else 0) + (1 if match.group(2) else 0)
        return f"{'#' * depth} {match.group(3).strip()}"

    text = _LATEX_ENV_RE.sub(_env, text)
    text = _LATEX_DISPLAY_RE.sub(lambda m: f"$${m.group(1).strip()}$$", text)
    text = _LATEX_INLINE_RE.sub(lambda m: f"${m.group(1).strip()}$", text)
    text = _LATEX_SECTION_RE.sub(_section, text)
    text = _LATEX_TEXTBF_RE.sub(r"**\1**", text)
    text = _LATEX_TEXTIT_RE.sub(r"*\1*", text)
    return text


def normalize_latex_markup(text: str) -> str:
    """Rewrite LaTeX delimiters and sectioning into Markdown-renderable form.

    ``\\[ x \\]`` becomes ``$$x$$`` and ``\\( x \\)`` becomes ``$x$``; Markdown
    renderers report "Undefined control sequence: \\[" for the originals. Fenced
    code blocks are left untouched — inside them the backslash form is content.
    """
    parts = _FENCE_SPLIT_RE.split(text)
    return "".join(
        part if part.startswith("```") else _normalize_latex_segment(part)
        for part in parts
    )


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
        repair_pdf_text_artifacts,
        normalize_latex_markup,
        replace_unicode_mojibake,
        dehyphenate_line_breaks,
        group_broken_paragraphs,
        strip_repeated_running_headers,
    )
    for step in steps:
        text = step(text)
    return text
