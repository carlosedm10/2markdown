"""Deterministic detection of math-like text spans — no model call involved.

A page whose *native* PDF text layer is math-dense (typically LaTeX-generated
course handouts) currently skips vision OCR entirely, because it has "enough"
text already — but that text is the raw glyph soup PDF extraction leaves
behind: "z = ¯x −µ0 σ/√n" instead of the rendered
"z = (x̄ - μ0) / (σ/√n)". Nothing here fixes that; this
module only finds it, cheaply and deterministically, so the caller (currently
`agents/page_review.py`'s `format_math()`, wired in `converter/pdf_ocr.py`)
can decide whether a page is worth a text-only LaTeX-formatting pass.

Two things are reported per page:

* `math_density` (0..1) — the share of whitespace-delimited tokens that look
  mathematical, by symbol or by context (see `find_math_spans`).
* `structure_lost` — whether some span shows the specific symptom of a
  fraction or square root whose LaTeX markup did not survive extraction: a
  bare "√" not attached to its radicand, or a run of short
  letter+digit/digit tokens ("s2 1 n1") that is what a split numerator and
  denominator look like once flattened to a single line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Greek and Greek Extended blocks, and the Unicode sub/superscript digits —
# none of these show up in ordinary prose, Spanish or English, so a token
# containing one is math-flagged directly, no context needed.
_GREEK_RANGES = "Ͱ-Ͽἀ-῿"
_SUBSUP_RANGES = "⁰-₟²³¹"
# Plus a fixed set of math-specific symbols: root, plus-minus, middle dot,
# times/divide, common relations, calculus operators, the Unicode minus sign
# (not the ASCII hyphen, which prose uses freely), the macron PDF extraction
# leaves for an overbar, and the handful of ASCII operators ('+', '=', '^',
# '_', braces, backslash) that are common in formulas but essentially never
# stand alone as their own prose token.
_SYMBOL_CHARS = "√±·×÷≤≥≠∞∑∫¯−" + r"+=^_{}\\"
_SYMBOL_RE = re.compile(f"[{_GREEK_RANGES}{_SUBSUP_RANGES}{_SYMBOL_CHARS}]")

_TOKEN_RE = re.compile(r"\S+")
# "s2", "n1", "x12", "k1" — a short letter run immediately followed by one or
# two digits. On its own this is as likely to be a footnote marker or a model
# name as a subscript; it is only promoted to math (see `find_math_spans`)
# when it sits among other math-flagged or candidate tokens.
_BARE_ALNUM_RE = re.compile(rf"^[A-Za-z{_GREEK_RANGES}]{{1,3}}\d{{1,2}}$")
_PURE_DIGIT_RE = re.compile(r"^\d{1,2}$")


@dataclass
class MathSpan:
    """One contiguous run of math-flagged tokens, as a slice of the page text."""

    start: int
    end: int
    text: str
    structure_lost: bool = False


@dataclass
class MathPageStats:
    spans: list[MathSpan] = field(default_factory=list)
    math_density: float = 0.0
    structure_lost: bool = False


def _token_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in _TOKEN_RE.finditer(text)]


def _is_symbol_token(token: str) -> bool:
    return bool(_SYMBOL_RE.search(token))


def _is_candidate_token(token: str) -> bool:
    return bool(_BARE_ALNUM_RE.match(token)) or bool(_PURE_DIGIT_RE.match(token))


def _span_structure_lost(tokens: list[str]) -> bool:
    """A fraction/root whose markup was flattened to bare tokens on one line.

    Two independent symptoms, either is enough:
    * a "√" that never merged with what follows it — the radicand fell
      on the far side of a space PDF extraction inserted;
    * the span is (almost) entirely short letter+digit / bare-digit tokens —
      "s2 1 n1" for what was s₁²/n₁, the numerator and
      denominator of two stacked fractions read left to right with no
      operator between them.
    """
    if any(tok == "√" for tok in tokens):
        return True
    if len(tokens) < 2:
        return False
    candidates = sum(1 for tok in tokens if _is_candidate_token(tok))
    return candidates >= 2 and candidates >= len(tokens) - 1


def find_math_spans(text: str, *, window: int = 2) -> list[MathSpan]:
    """Math-like spans in `text`, merging adjacent flagged tokens.

    Detection is two passes:
    1. Any token containing a math-specific symbol (Greek letter, root,
       relation, sub/superscript digit, ...) is flagged directly.
    2. A "bare alnum" token ("s2", "1", "n1") is promoted to math only when a
       flagged token sits within `window` tokens of it, or it is itself part
       of a run of 3+ such candidates — the burst that marks a flattened
       fraction (see `_span_structure_lost`). This keeps ordinary prose
       (page numbers, footnote markers, "COVID19") from tripping the
       detector on its own.
    """
    tokens = _token_spans(text)
    if not tokens:
        return []

    n = len(tokens)
    flags = [_is_symbol_token(tok) for _, _, tok in tokens]
    candidate = [_is_candidate_token(tok) for _, _, tok in tokens]

    for i in range(n):
        if flags[i] or not candidate[i]:
            continue
        lo, hi = max(0, i - window), min(n, i + window + 1)
        if any(flags[j] for j in range(lo, hi)):
            flags[i] = True
        elif sum(candidate[lo:hi]) >= 3:
            flags[i] = True

    spans: list[MathSpan] = []
    i = 0
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        start, end = tokens[i][0], tokens[j][1]
        span_tokens = [tok for _, _, tok in tokens[i : j + 1]]
        spans.append(
            MathSpan(
                start=start,
                end=end,
                text=text[start:end],
                structure_lost=_span_structure_lost(span_tokens),
            )
        )
        i = j + 1
    return spans


def analyze(text: str) -> MathPageStats:
    """`math_density` and `structure_lost` for one page's text, plus the spans."""
    tokens = _token_spans(text)
    if not tokens:
        return MathPageStats()
    spans = find_math_spans(text)
    flagged_tokens = sum(
        1
        for start, end, _ in tokens
        if any(sp.start <= start and end <= sp.end for sp in spans)
    )
    density = flagged_tokens / len(tokens)
    structure_lost = any(sp.structure_lost for sp in spans)
    return MathPageStats(
        spans=spans, math_density=density, structure_lost=structure_lost
    )


def math_density(text: str) -> float:
    return analyze(text).math_density
