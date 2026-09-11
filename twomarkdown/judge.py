"""Text-only LLM judge for mathematically implausible content in converted .md files.

An OCR pass (vision model) transcribes handwritten technical notes. Separately,
this module asks a plain TEXT model — no image, only the produced Markdown — to
flag passages whose mathematics does not add up: an equation that cannot balance,
a step that does not follow, a symbol inconsistent with the rest of the passage.
Transcription and verification are different tasks, and a text-only pass catches
things a combined "transcribe and double-check" vision prompt misses: a vision
model transcribed a handwritten "H + g + C = 0" (C = constant of integration) as
"H + g + 4 = 0" and could not resolve its own glyph, but a text-only qwen2.5:14b
given just that markdown line flagged it immediately as mathematically senseless
in the context of a differential equation.

CRITICAL SCOPE LIMIT: this judge never sees the source page. It has no reference
image, so it CANNOT verify fidelity to the original — it can only assess whether
the transcribed text is internally plausible on its own terms. It cannot tell a
transcription error from the student's own shorthand, simplification, or mistake:
both look identical on the page. For that reason its output is a REVIEW QUEUE for
a human to check, never an automatic correction. This module never modifies,
patches, or rewrites the .md files it reads.

Talks to host Ollama (`llm_config.ollama_base_url`, OpenAI-compatible) via a
direct, explicitly-timed POST to ``/chat/completions``. An untimed request can
hang the batch forever waiting on a dropped connection; every call here sets an
explicit connect and read timeout, and any failure — timeout, connection error,
malformed or unparseable reply — degrades to "no flags for this section" with a
logged warning. The judge must never crash or hang a batch.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from twomarkdown.agents.image_ocr import strip_wrapping_fence
from twomarkdown.config import llm_config

logger = logging.getLogger(__name__)

# A text model is fast enough (~16s/page measured) that a generous vision-scale
# timeout is unnecessary, but still explicit: an untimed request can hang forever.
DEFAULT_JUDGE_MODEL = "qwen2.5:14b"
DEFAULT_CONNECT_TIMEOUT_SEC = 15.0
DEFAULT_REQUEST_TIMEOUT_SEC = 120.0

# Below this many characters of body text a section is almost certainly a bare
# heading, a figure caption, or a near-empty page: not worth a model round trip.
MIN_SECTION_CHARS = 80

JUDGE_SYSTEM_PROMPT = """
You are a mathematics reviewer checking transcribed lecture or course notes for
content that is mathematically implausible or internally inconsistent.

You are given ONLY transcribed text, with NO image of the original page. You
cannot check whether the transcription is faithful to the source, and you must
never assume it is unfaithful just because something looks odd: judge the
mathematics on its own terms, as written.

Flag a passage ONLY when the mathematics itself does not add up, for example:
- an equation whose two sides cannot be equal for any value of the symbols,
- a step whose conclusion does not follow from the line before it,
- a term, sign, or symbol that breaks internal consistency with the rest of the
  passage (for example a stray digit where every other equation in the same
  derivation uses a named constant).

Never flag:
- notation, symbol choice, or style you would simply have written differently,
- a valid alternative convention, variable name, or unit system,
- anything you cannot verify without seeing the source image — that is out of
  scope for you; leave it alone rather than guessing.

Respond with ONLY a JSON array, no prose before or after it, one object per
flagged passage:
[{"quote": "<exact text you are flagging, copied verbatim from the passage>",
  "issue": "<one sentence: why it cannot be mathematically right>",
  "suggestion": "<the likely intended form, or an empty string if unclear>"}]

If there is nothing worth flagging, respond with exactly: sin incidencias
"""

_LANGUAGE_INSTRUCTION = {
    "es": "Responde en español.",
    "en": "Answer in English.",
}
_DEFAULT_LANGUAGE_INSTRUCTION = "Answer in the same language as the passage below."

_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\b.*$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^## Page \d+\b.*\n?")
_FRONTMATTER_LANGUAGE_RE = re.compile(
    r'^language:\s*"?([A-Za-z-]+)"?\s*$', re.MULTILINE
)

_CLEAN_TOKENS = frozenset(
    {"sin incidencias", "no issues", "no issues found", "[]", "none", "ninguna"}
)

# Long pages produce long JSON; the default budget cuts it off mid-string.
JUDGE_MAX_TOKENS = 2048

# Files judge_tree must never treat as input: its own report, and the batch's
# manifest / trace / export report siblings (see docs/README.md taxonomy).
_SKIP_FILE_NAMES = frozenset({"review-queue.md"})


@dataclass(frozen=True)
class Flag:
    """One candidate issue for human review. Never applied automatically."""

    page: int
    quoted_text: str
    suggestion: str
    reason: str


def _language_instruction(language: str | None) -> str:
    if language is None:
        return _DEFAULT_LANGUAGE_INSTRUCTION
    return _LANGUAGE_INSTRUCTION.get(language, _DEFAULT_LANGUAGE_INSTRUCTION)


def _split_into_pages(text: str) -> list[tuple[int, str]]:
    """Split a document into ``## Page N`` sections, tagged with their number.

    Text before the first heading (frontmatter, a title) is dropped: it is not
    page content. A document with no page headings at all is treated as one
    page numbered 1, so non-paginated markdown still gets judged.
    """
    matches = list(_PAGE_HEADING_RE.finditer(text))
    if not matches:
        return [(1, text)] if text.strip() else []
    sections: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((int(match.group(1)), text[start:end]))
    return sections


def _section_body(section: str) -> str:
    """Section text with its ``## Page N`` heading line removed."""
    return _HEADING_LINE_RE.sub("", section, count=1).strip()


def _extract_json(text: str) -> Any | None:
    """Parse a JSON value, tolerating a model that wraps it in stray prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _salvage_objects(text: str) -> list[dict] | None:
    """Recover complete {...} objects from a reply whose JSON array was cut short.

    A truncated array is unparseable as a whole, but the objects before the cut
    are intact and carry real findings; dropping them loses the page entirely.
    """
    objects: list[dict] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    objects.append(parsed)
                start = None
    return objects or None


def _parse_judge_reply(reply: str, page: int) -> list[Flag]:
    """Turn a model reply into Flags, degrading to no flags on anything unparseable.

    Small models frequently ignore "respond with only JSON" and return prose, a
    trailing sentence, or a clean-signal token instead. None of that should ever
    raise: worst case a section that had something to flag is silently skipped.
    """
    cleaned = strip_wrapping_fence(reply or "").strip()
    if not cleaned:
        return []
    if cleaned.strip(" .[]\"'`").lower() in _CLEAN_TOKENS:
        return []

    data = _extract_json(cleaned)
    if data is None:
        salvaged = _salvage_objects(cleaned)
        if salvaged:
            logger.warning(
                "Judge reply for page %s was truncated; salvaged %s complete flag(s)",
                page,
                len(salvaged),
            )
            data = salvaged
    if data is None:
        logger.warning(
            "Judge reply for page %s was not parseable JSON, skipping: %r",
            page,
            cleaned[:200],
        )
        return []

    items: Any = data
    if isinstance(data, dict):
        items = data.get("flags", [])
    if not isinstance(items, list):
        logger.warning("Judge reply for page %s had no flag list, skipping", page)
        return []

    flags: list[Flag] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        reason = str(item.get("issue") or item.get("reason") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        if not quote or not reason:
            continue
        flags.append(
            Flag(page=page, quoted_text=quote, suggestion=suggestion, reason=reason)
        )
    return flags


def _request_completion(
    user_message: str,
    *,
    model: str,
    base_url: str,
    connect_timeout: float,
    request_timeout: float,
) -> str | None:
    """POST one judge request to Ollama's OpenAI-compatible endpoint.

    Returns None (never raises) on timeout, connection failure, an HTTP error
    status, or a response shaped unlike a chat completion — any of which must
    degrade the section to "no flags", not abort or hang the batch.
    """
    timeout = httpx.Timeout(request_timeout, connect=connect_timeout)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        # Without an explicit budget Ollama truncates the JSON array mid-string
        # and every flag on that page is lost.
        "max_tokens": JUDGE_MAX_TOKENS,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base_url}/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        logger.warning("Judge request to %s timed out (model=%s)", base_url, model)
        return None
    except httpx.HTTPError as exc:
        logger.warning("Judge request to %s failed: %s", base_url, exc)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Judge response from %s was malformed: %s", base_url, exc)
        return None
    if not isinstance(content, str):
        logger.warning("Judge response from %s had non-text content", base_url)
        return None
    return content


def judge_markdown(
    text: str,
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    language: str | None = None,
    base_url: str | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SEC,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    min_section_chars: int = MIN_SECTION_CHARS,
) -> list[Flag]:
    """Judge a converted document, one ``## Page N`` section at a time.

    Judging per page keeps flags attributed to a page number and keeps each
    request small, so a long document never blows the model's context window.
    Sections with little text are skipped without a model call. Any failure
    for a given section (timeout, connection error, unparseable reply) yields
    no flags for that section only; it never raises and never aborts the rest
    of the document.
    """
    resolved_base_url = base_url or llm_config.ollama_base_url
    instruction = _language_instruction(language)

    flags: list[Flag] = []
    for page, section in _split_into_pages(text or ""):
        body = _section_body(section)
        if len(body) < min_section_chars:
            continue
        user_message = (
            f"{instruction}\n\n"
            "Review the following transcribed passage. Flag only mathematically "
            "implausible or internally inconsistent content, per your "
            "instructions.\n\n"
            f"{body}"
        )
        reply = _request_completion(
            user_message,
            model=model,
            base_url=resolved_base_url,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
        )
        if reply is None:
            continue
        flags.extend(_parse_judge_reply(reply, page))
    return flags


def _frontmatter_language(text: str) -> str | None:
    """Read the ``language:`` field out of a converted file's YAML frontmatter."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    frontmatter = text if end == -1 else text[:end]
    match = _FRONTMATTER_LANGUAGE_RE.search(frontmatter)
    return match.group(1) if match else None


def _is_skippable(root: Path, md_path: Path) -> bool:
    if md_path.name in _SKIP_FILE_NAMES:
        return True
    return any(part.startswith(".") for part in md_path.relative_to(root).parts)


def judge_tree(
    root: Path,
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    base_url: str | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SEC,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    min_section_chars: int = MIN_SECTION_CHARS,
) -> dict[Path, list[Flag]]:
    """Judge every converted ``.md`` file under a ``*_2markdown`` output tree.

    Skips dot-directories (e.g. ``.unzipped/`` staged zip members) and this
    module's own generated review queue, so re-running judge_tree never judges
    its own report. Only files that produced at least one flag are included in
    the result.
    """
    results: dict[Path, list[Flag]] = {}
    for md_path in sorted(root.rglob("*.md")):
        if _is_skippable(root, md_path):
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s, skipping: %s", md_path, exc)
            continue

        language = _frontmatter_language(text)
        flags = judge_markdown(
            text,
            model=model,
            language=language,
            base_url=base_url,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            min_section_chars=min_section_chars,
        )
        if flags:
            results[md_path] = flags
    return results


def format_review_queue(results: dict[Path, list[Flag]]) -> str:
    """Render judge_tree's results as a review-queue markdown document.

    Grouped by file, then by page. This is a checklist for a human, never a
    patch: the judge cannot see the source page, so a flagged passage may be a
    genuine transcription slip, or it may be the student's own shorthand or
    error, transcribed perfectly. Either way a person decides, not this module.
    """
    lines = [
        "# Review queue",
        "",
        "Generated by the text-only judge. Every item below is a candidate for "
        "human review, not an automatic correction: the judge never sees the "
        "source page image, so it cannot tell a transcription error from the "
        "student's own shorthand or mistake. The `.md` files are unchanged.",
        "",
    ]

    flagged_files = {path: flags for path, flags in results.items() if flags}
    if not flagged_files:
        lines.append("No files were flagged.")
        lines.append("")
        return "\n".join(lines)

    for path in sorted(flagged_files, key=str):
        flags = flagged_files[path]
        lines.append(f"## {path}")
        lines.append("")

        by_page: dict[int, list[Flag]] = {}
        for flag in flags:
            by_page.setdefault(flag.page, []).append(flag)

        for page in sorted(by_page):
            lines.append(f"### Page {page}")
            lines.append("")
            for flag in by_page[page]:
                lines.append(f'- **Quoted:** "{flag.quoted_text}"')
                lines.append(f"  **Why:** {flag.reason}")
                if flag.suggestion:
                    lines.append(f"  **Suggested fix:** {flag.suggestion}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI: review a converted output tree and write review-queue.md beside it."""
    import argparse

    parser = argparse.ArgumentParser(description="LLM review of converted markdown.")
    parser.add_argument("root", type=Path, help="a *_2markdown output directory")
    parser.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the queue (default: <root>/review-queue.md)",
    )
    args = parser.parse_args(argv)

    results = judge_tree(args.root, model=args.model)
    report = format_review_queue(results)

    out = args.out or (args.root / "review-queue.md")
    try:
        out.write_text(report, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write %s: %s", out, exc)
        print(report)
        return 0

    flagged = sum(len(v) for v in results.values())
    print(f"Reviewed {args.root}: {flagged} flag(s) across {len(results)} file(s).")
    print(f"Review queue: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
