"""Lightweight language guess for YAML frontmatter (no extra deps)."""

from __future__ import annotations

import re

_ES_HINTS = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "de",
        "que",
        "y",
        "en",
        "un",
        "una",
        "para",
        "con",
        "por",
        "como",
        "más",
        "pero",
        "este",
        "esta",
        "también",
        "porque",
    }
)
_EN_HINTS = frozenset(
    {
        "the",
        "and",
        "of",
        "to",
        "in",
        "a",
        "is",
        "that",
        "for",
        "on",
        "with",
        "as",
        "this",
        "are",
        "was",
        "be",
        "by",
        "from",
        "or",
        "an",
    }
)
_TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ']+")

# Maths is not prose. LaTeX subscripts alone ("a_n", "a_1") contributed 86 of 112
# "English" hits on a Spanish page, and \in / \to collide with English stopwords,
# so a maths-heavy document was reported as English.
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MATH_SPAN = re.compile(r"\$\$.+?\$\$|\$[^$\n]+\$", re.DOTALL)
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")


def strip_non_prose(text: str) -> str:
    """Remove code, maths and links so only natural language is counted."""
    text = _CODE_FENCE.sub(" ", text or "")
    text = _MARKDOWN_LINK.sub(" ", text)
    text = _MATH_SPAN.sub(" ", text)
    return _LATEX_COMMAND.sub(" ", text)


def guess_language(text: str, *, sample_chars: int = 4000) -> str | None:
    """Return 'es', 'en', or None when there is too little signal."""
    sample = strip_non_prose(text or "")[:sample_chars].lower()
    tokens = _TOKEN.findall(sample)
    if len(tokens) < 20:
        return None
    es = sum(1 for t in tokens if t in _ES_HINTS)
    en = sum(1 for t in tokens if t in _EN_HINTS)
    if es == 0 and en == 0:
        return None
    if es >= en * 1.2 and es >= 4:
        return "es"
    if en >= es * 1.2 and en >= 4:
        return "en"
    return None
