"""Proofread a page's transcription using nothing but the transcription.

A vision model reading dense handwriting gets a few characters wrong in ways the
surrounding text gives away: a symbol one letter off from the one used on every
other line, a Greek letter written as the Latin one it resembles, a bracket left
open, a LaTeX command that will not render. None of that needs the page to spot.

The pass is deliberately blind, and the constraint that makes it safe is the same
one: a reviewer that cannot see the page cannot know what is missing from it, so
it has no business adding anything. Correcting a character is useful; supplying a
line is invention. That is asked for in the prompt and enforced here, because an
earlier attempt at text-only correction was given room to rewrite and made the
transcription worse.

Its failure mode is "no change", never "worse": anything that does not look like
light proofreading is discarded and the transcription is kept.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
from collections import Counter

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.exceptions import ModelAPIError

from twomarkdown.config import llm_config
from twomarkdown.converter.math_density import MathSpan
from twomarkdown.prompts import MATH_FORMAT_PROMPT, PAGE_REVIEW_PROMPT

logger = logging.getLogger(__name__)

_local = threading.local()


class PageReview(BaseModel):
    """A proofread transcription, and what the reviewer says it changed."""

    markdown: str = Field(description="The full text with characters corrected.")
    changes: list[str] = Field(
        default_factory=list,
        description="One short line per correction; empty when nothing changed.",
    )


def review_enabled() -> bool:
    return bool(llm_config.llm_enabled and llm_config.review_model.strip())


def _agent() -> Agent[None, PageReview]:
    """Reviewer for the calling thread, built like every other agent here."""
    agent = getattr(_local, "agent", None)
    if agent is None:
        from pydantic_ai.models import infer_model

        from twomarkdown.agents.image_ocr import _provider_factory, normalize_model_id

        agent = Agent(
            model=infer_model(
                normalize_model_id(llm_config.review_model),
                provider_factory=_provider_factory,
            ),
            system_prompt=PAGE_REVIEW_PROMPT.strip(),
            # PromptedOutput, not the bare model: asking for `PageReview`
            # directly makes pydantic-ai request the schema as a tool call, and
            # Ollama answers "does not support tools" for every vision model it
            # serves. This keeps both a hosted and a local reviewer usable.
            output_type=PromptedOutput(PageReview),
            # Greedy, like transcription. Left to sample, the pass corrected a
            # dropped prime on one run and missed it on the next over the same
            # text: a proofreader whose answer changes between runs cannot be
            # audited, and its output cannot be told from noise.
            model_settings={"temperature": 0.0, "max_tokens": 4096},
        )
        _local.agent = agent
    return agent


_MATH_SPAN_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.DOTALL)
_LATEX_COMMAND_RE = re.compile(r"\\([a-zA-Z]+)")


def latex_commands_used(text: str) -> list[str]:
    """Every distinct LaTeX command inside the text's maths, in sorted order.

    The reviewer knows perfectly well that \\arccot is not a command — asked to
    write renderable LaTeX it reaches for \\operatorname unprompted. What it does
    not do is notice, mid-proofread, that one command out of forty is made up.
    Listing them turns a question of attention into a question it can answer.

    Extraction only: this encodes no opinion about which commands are real, so it
    cannot go stale the way a table of known-bad names would.
    """
    seen: set[str] = set()
    for match in _MATH_SPAN_RE.finditer(text):
        span = match.group(1) or match.group(2) or ""
        seen.update(_LATEX_COMMAND_RE.findall(span))
    return sorted(seen)


def _line_counts(text: str) -> tuple[int, int]:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return len(lines), sum(len(line) for line in lines)


def is_light_proofreading(original: str, reviewed: str) -> bool:
    """True when `reviewed` differs from `original` only by small character edits.

    Two independent checks, because the prompt alone does not bind a model:

    * The same number of non-blank lines. A blind reviewer adding or dropping a
      line is either inventing content or deleting the student's, and it cannot
      have grounds for either — it never saw the page.
    * High character-level similarity. Rewording a paragraph keeps the line count
      and would otherwise pass; changing a symbol barely moves the ratio.
    """
    if not reviewed.strip():
        return False
    if not original.strip():
        return False
    if _line_counts(original)[0] != _line_counts(reviewed)[0]:
        return False
    ratio = difflib.SequenceMatcher(None, original, reviewed).ratio()
    return ratio >= llm_config.review_min_similarity


def describe_changes(original: str, reviewed: str) -> list[str]:
    """What actually changed, read from the two texts rather than asked for.

    A reviewer's own account of its work is not evidence: asked on one page, a
    model reported fixing a symbol that was already correct, reported a second
    change that never happened, and did not mention the duplicate line it really
    did remove. A log that flatters the pass is worse than none, because it reads
    as work done.
    """
    changes: list[str] = []
    matcher = difflib.SequenceMatcher(
        None, original.strip().splitlines(), reviewed.strip().splitlines()
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = " / ".join(original.strip().splitlines()[i1:i2]).strip()
        after = " / ".join(reviewed.strip().splitlines()[j1:j2]).strip()
        changes.append(f"{before[:110]}  ->  {after[:110]}")
    return changes


def describe_changes_structured(
    original: str, reviewed: str
) -> list[dict[str, object]]:
    """Same diff as `describe_changes()`, kept structured for `EventPageDone`.

    `describe_changes()` already flattens each change into one log-friendly
    line ("before -> after"); the WS event instead wants `{line, before,
    after}` (`schemas.ReviewChange`), so this re-walks the same opcodes rather
    than parsing that string back apart.
    """
    changes: list[dict[str, object]] = []
    original_lines = original.strip().splitlines()
    reviewed_lines = reviewed.strip().splitlines()
    matcher = difflib.SequenceMatcher(None, original_lines, reviewed_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "line": i1 + 1,
                "before": " / ".join(original_lines[i1:i2]).strip(),
                "after": " / ".join(reviewed_lines[j1:j2]).strip(),
            }
        )
    return changes


def _review_request(transcription: str) -> str:
    """The text to proofread, plus the LaTeX commands it uses."""
    commands = latex_commands_used(transcription)
    parts = [f"The transcription to proofread:\n\n{transcription}"]
    if commands:
        parts.append(
            "LaTeX commands this text uses: "
            + ", ".join("\\" + name for name in commands)
            + ".\nAny of these that is not a real command will not render, and "
            "the formula is lost. Write such a name with \\operatorname instead. "
            "Leave the ones that are real exactly as they are."
        )
    return "\n\n".join(parts)


def review_page(
    transcription: str, *, model: str | None = None
) -> tuple[str, list[str]]:
    """Proofread one page's transcription. Returns the text and what changed.

    Falls back to the original — with no changes — whenever the reviewer errors,
    returns nothing, or returns something that is not light proofreading.
    """
    if not review_enabled() or not transcription.strip():
        return transcription, []

    from twomarkdown.agents.image_ocr import (
        _call_with_backoff,
        _is_out_of_quota,
        _page_ocr_lock,
        _remote_lock,
        is_local_model,
    )
    from twomarkdown.batch import events

    name = model or llm_config.review_model
    permit = _page_ocr_lock if is_local_model(name) else _remote_lock
    try:
        with events.stage("review"), permit:
            result = _call_with_backoff(
                lambda: _agent().run_sync(_review_request(transcription)),
                what="Page review",
                model=name,
            )
    except ModelAPIError as exc:
        logger.warning("Page review API error: %s", exc)
        if not is_local_model(name) and _is_out_of_quota(exc):
            from twomarkdown.converter import ocr as ocr_mod

            message = f"Revisión omitida: saldo agotado en {name}"
            ocr_mod.record_soft_failure(message)
            events.log("warning", message)
        return transcription, []
    except Exception as exc:
        logger.warning("Page review failed: %s", exc)
        return transcription, []

    from twomarkdown.agents.image_ocr import _report_cloud_cost

    _report_cloud_cost(name, result)

    reviewed = result.output.markdown
    if reviewed.strip() == transcription.strip():
        # It answered, and the text needed nothing. Any "changes" it lists are
        # invented: a reviewer asked what it corrected will describe corrections
        # whether or not it made them.
        return transcription, []
    if not is_light_proofreading(transcription, reviewed):
        logger.warning(
            "Page review edited beyond proofreading (%s -> %s chars, %s -> %s "
            "lines); keeping the transcription",
            len(transcription),
            len(reviewed or ""),
            _line_counts(transcription)[0],
            _line_counts(reviewed or "")[0],
        )
        return transcription, []

    return reviewed, describe_changes(transcription, reviewed)


# ---------------------------------------------------------------------------
# format_math() — a second, separate task for the same text-only reviewer:
# rewrite math-like spans (converter/math_density.py) as LaTeX. Kept apart
# from review_page()/PageReview above because the two tasks have unrelated
# contracts: proofreading may only edit characters and is checked by
# character-level similarity; formatting may rewrite a span's *notation*
# wholesale (bare symbols -> \frac{...}{...}) and is checked by a completely
# different guard — same multiset of letters/digits/Greek names in and out.
# ---------------------------------------------------------------------------


class MathSpanRewrite(BaseModel):
    index: int
    latex: str = Field(description="The span rewritten as LaTeX ($...$ or $$...$$).")


class MathFormatOutput(BaseModel):
    """One rewrite per span the model could confidently format."""

    spans: list[MathSpanRewrite] = Field(default_factory=list)


def _math_agent() -> Agent[None, MathFormatOutput]:
    """Math-formatting reviewer for the calling thread. Same model, own prompt."""
    agent = getattr(_local, "math_agent", None)
    if agent is None:
        from pydantic_ai.models import infer_model

        from twomarkdown.agents.image_ocr import _provider_factory, normalize_model_id

        agent = Agent(
            model=infer_model(
                normalize_model_id(llm_config.review_model),
                provider_factory=_provider_factory,
            ),
            system_prompt=MATH_FORMAT_PROMPT.strip(),
            output_type=PromptedOutput(MathFormatOutput),
            model_settings={"temperature": 0.0, "max_tokens": 4096},
        )
        _local.math_agent = agent
    return agent


_GREEK_UNICODE_TO_NAME = {
    "µ": "mu",
    "μ": "mu",
    "σ": "sigma",
    "Σ": "sigma",
    "α": "alpha",
    "β": "beta",
    "λ": "lambda",
    "π": "pi",
    "θ": "theta",
    "φ": "phi",
    "δ": "delta",
    "ε": "epsilon",
    "γ": "gamma",
    "Ω": "omega",
    "ω": "omega",
    "τ": "tau",
    "ρ": "rho",
    "ν": "nu",
    "χ": "chi",
    "ψ": "psi",
    "ξ": "xi",
    "ζ": "zeta",
    "η": "eta",
    "κ": "kappa",
    "ι": "iota",
    "υ": "upsilon",
}
_GREEK_COMMAND_TO_NAME = {
    f"\\{name}": name for name in sorted(set(_GREEK_UNICODE_TO_NAME.values()))
}
_SUBSUP_TO_DIGIT = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}  # fmt: skip
_ALNUM_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")
_LATEX_COMMAND_STRIP_RE = re.compile(r"\\[a-zA-Z]+")
# Operator-name commands spell out a literal word that is also what the *plain*
# span reads ("min" on the page becomes "\min" in LaTeX) — unlike a purely
# notational command such as \frac or \cdot, which has no letter-for-letter
# counterpart in the unformatted span. Blanket-stripping every "\word" (as
# `_LATEX_COMMAND_STRIP_RE` does below) silently drops that word from the
# LaTeX side's multiset while the plain side still counts it, so a span
# containing "min", "log", "lim"... was rejected even when the rewrite was
# exactly right. Unwrapped first, so the stripped regex below never sees them.
_OPERATOR_NAME_COMMANDS = (
    "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "csc", "deg",
    "det", "dim", "exp", "gcd", "hom", "inf", "ker", "lg", "lim", "liminf",
    "limsup", "ln", "log", "max", "min", "mod", "Pr", "sec", "sin", "sinh",
    "sup", "tan", "tanh",
)  # fmt: skip
_OPERATOR_NAME_RE = re.compile(
    r"\\(" + "|".join(_OPERATOR_NAME_COMMANDS) + r")\b"
)


def _alnum_tokens(text: str) -> list[str]:
    """Letters and digits in `text`, Greek symbols spelled out, lowercased.

    Used on both sides of `format_math()`'s guard: the original span (bare
    Unicode) and the model's LaTeX rewrite (`_latex_alnum_tokens` below) must
    reduce to the same multiset, or the rewrite is rejected. Letters and
    digits are separate tokens ("mu0" -> ["mu", "0"]) so a subscript survives
    the comparison the same way on both sides, whether it arrives as a
    Unicode digit glued to its base ("µ0") or as LaTeX's "\\mu_0".
    """
    for symbol, name in _GREEK_UNICODE_TO_NAME.items():
        text = text.replace(symbol, f" {name} ")
    for symbol, digit in _SUBSUP_TO_DIGIT.items():
        text = text.replace(symbol, digit)
    return [tok.lower() for tok in _ALNUM_TOKEN_RE.findall(text)]


def _latex_alnum_tokens(latex: str) -> list[str]:
    """Same multiset as `_alnum_tokens`, read out of a LaTeX rewrite instead.

    Greek commands (\\mu, \\sigma, ...) are spelled out the same as their
    Unicode counterparts; every other command (\\frac, \\sqrt, \\bar,
    \\operatorname, ...) is structural and is stripped rather than counted.
    """
    for command, name in _GREEK_COMMAND_TO_NAME.items():
        latex = latex.replace(command, f" {name} ")
    latex = _OPERATOR_NAME_RE.sub(r" \1 ", latex)
    latex = _LATEX_COMMAND_STRIP_RE.sub(" ", latex)
    return [tok.lower() for tok in _ALNUM_TOKEN_RE.findall(latex)]


def _math_request(page_text: str, spans: list[MathSpan]) -> str:
    lines = [
        f"Full page text for context:\n\n{page_text}",
        "",
        "Spans to rewrite as LaTeX (index: original text):",
    ]
    for i, sp in enumerate(spans):
        lines.append(f"{i}: {sp.text!r}")
    return "\n".join(lines)


def format_math(
    page_text: str, spans: list[MathSpan], *, model: str | None = None
) -> tuple[str, list[str]]:
    """Rewrite `spans` of `page_text` as LaTeX, guarded per-span.

    Every accepted rewrite keeps the exact same multiset of letters, digits
    and Greek names as the span it replaces (`_alnum_tokens` vs.
    `_latex_alnum_tokens`) — nothing added, nothing dropped, only re-typeset.
    A span the model skipped, or whose rewrite fails that guard, is left as
    written with an `<!-- revisar: fórmula -->` marker appended after it, so
    a reader (or the desktop app) can find what still needs a human look.

    Falls back to `(page_text, [])` whenever the reviewer is disabled, there
    is nothing to format, or the call itself fails — same "no change, never
    worse" contract as `review_page()`.
    """
    if not review_enabled() or not spans or not page_text.strip():
        return page_text, []

    from twomarkdown.agents.image_ocr import (
        _call_with_backoff,
        _is_out_of_quota,
        _page_ocr_lock,
        _remote_lock,
        is_local_model,
    )
    from twomarkdown.batch import events

    name = model or llm_config.review_model
    permit = _page_ocr_lock if is_local_model(name) else _remote_lock
    try:
        with events.stage("math_format"), permit:
            result = _call_with_backoff(
                lambda: _math_agent().run_sync(_math_request(page_text, spans)),
                what="Math format",
                model=name,
            )
    except ModelAPIError as exc:
        logger.warning("Math format API error: %s", exc)
        if not is_local_model(name) and _is_out_of_quota(exc):
            from twomarkdown.converter import ocr as ocr_mod

            message = f"Formateo de matemáticas omitido: saldo agotado en {name}"
            ocr_mod.record_soft_failure(message)
            events.log("warning", message)
        return page_text, []
    except Exception as exc:
        logger.warning("Math format failed: %s", exc)
        return page_text, []

    from twomarkdown.agents.image_ocr import _report_cloud_cost

    _report_cloud_cost(name, result)

    rewrites = {r.index: r.latex for r in result.output.spans}
    accepted = 0
    rejected = 0
    changes: list[str] = []
    text = page_text
    # Right to left, so accepting or marking an earlier span never shifts the
    # offsets of one not yet applied.
    for i, span in sorted(enumerate(spans), key=lambda p: p[1].start, reverse=True):
        latex = rewrites.get(i)
        # Multiset equality, not order-sensitive: "nothing added, nothing
        # dropped" is what the guard means, not "in the same order".
        ok = bool(latex) and Counter(_alnum_tokens(span.text)) == Counter(
            _latex_alnum_tokens(latex or "")
        )
        if ok:
            text = text[: span.start] + latex + text[span.end :]
            accepted += 1
            changes.append(f"{span.text[:60]!r} -> {latex[:60]!r}")
        else:
            marker = " <!-- revisar: fórmula -->"
            text = text[: span.end] + marker + text[span.end :]
            rejected += 1

    events.log(
        "info", f"Math format: {accepted} span(s) formatted, {rejected} left as-is"
    )
    return text, changes
