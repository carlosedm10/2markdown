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

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.exceptions import ModelAPIError

from twomarkdown.config import llm_config
from twomarkdown.prompts import PAGE_REVIEW_PROMPT

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
        _page_ocr_lock,
        _remote_lock,
        is_local_model,
    )

    name = model or llm_config.review_model
    permit = _page_ocr_lock if is_local_model(name) else _remote_lock
    try:
        with permit:
            result = _call_with_backoff(
                lambda: _agent().run_sync(_review_request(transcription)),
                what="Page review",
                model=name,
            )
    except ModelAPIError as exc:
        logger.warning("Page review API error: %s", exc)
        return transcription, []
    except Exception as exc:
        logger.warning("Page review failed: %s", exc)
        return transcription, []

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
