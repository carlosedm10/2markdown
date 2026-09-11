"""Deterministic, model-free quality gate for produced ``.md`` files.

Every rule here was found by hand while debugging real converter output: a
stray NUL byte that made git treat the file as binary, leftover LaTeX escapes
a Markdown renderer chokes on, an odd count of ``$$`` delimiters, a page whose
text layer came out shredded, orphan accent glyphs sitting next to their
letter instead of composed onto it, a body that is empty after frontmatter,
a figure image link pointing at nothing, and a generated caption in the wrong
language. Each is regex/heuristic-detectable, so this module needs no LLM and
no network, and it is cheap enough to run over every file in a
``*_2markdown/`` output tree.

Public API:

- ``validate_markdown(path, text) -> list[Finding]`` — run every rule over one
  file's already-read text.
- ``validate_tree(root) -> list[Finding]`` — walk a ``*_2markdown`` output
  directory and run ``validate_markdown`` over each ``.md`` file in it.
- ``format_report(findings) -> str`` — a short, plain-text, grouped summary.

A validator that crashes on bad input is useless: every rule is defensive and
returns findings instead of raising, and ``validate_tree`` never aborts a
batch because one file could not be read.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from twomarkdown.converter.pdf_ocr import is_scrambled_text
from twomarkdown.language import guess_language

# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

Severity = str  # "error" | "warning"


@dataclass(frozen=True)
class Finding:
    """One deterministic issue found in a converted markdown file."""

    path: Path
    rule: str
    severity: Severity
    detail: str
    line: int | None = None


def _finding_sort_key(finding: Finding) -> tuple:
    return (
        str(finding.path),
        finding.rule,
        finding.line if finding.line is not None else -1,
        finding.detail,
    )


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=_finding_sort_key)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _safe(fn, *args) -> list[Finding]:
    """Run one rule, swallowing any exception so a single bad rule/file never
    aborts the batch — a crash is reported as a finding instead of raised."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - defensive by design, see module docstring
        path = args[0]
        name = fn.__name__.removeprefix("_check_")
        return [
            Finding(
                path=path,
                rule=f"{name}_internal_error",
                severity="warning",
                detail=f"validator rule raised {exc.__class__.__name__}: {exc}",
            )
        ]


# ---------------------------------------------------------------------------
# Fenced-code helper (rules 2 and 3 must not fire inside ``` ... ``` blocks)
# ---------------------------------------------------------------------------


def _strip_fenced_code(text: str) -> str:
    """Blank out the interior of fenced code blocks, keeping line numbers intact.

    Fence lines themselves (the ``` markers) are kept so line numbers do not
    shift; only the content between them is replaced with an empty line.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    marker = ""
    for line in lines:
        stripped = line.strip()
        if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
            in_fence = True
            marker = stripped[:3]
            out.append(line)
            continue
        if in_fence:
            if stripped.startswith(marker):
                in_fence = False
                out.append(line)
            else:
                out.append("")
            continue
        out.append(line)
    return "\n".join(out)


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body) — frontmatter is "" when absent."""
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return "", text or ""
    return text[: match.end()], text[match.end() :]


# ---------------------------------------------------------------------------
# 1. control_chars (error)
# ---------------------------------------------------------------------------

_C0_ALLOWED = {"\n", "\t"}


def _check_control_chars(path: Path, text: str) -> list[Finding]:
    counts: Counter[str] = Counter()
    for ch in text:
        if ord(ch) < 0x20 and ch not in _C0_ALLOWED:
            counts[ch] += 1

    findings: list[Finding] = []
    for ch in sorted(counts, key=ord):
        count = counts[ch]
        codepoint = ord(ch)
        name = "NUL" if codepoint == 0 else f"U+{codepoint:04X}"
        findings.append(
            Finding(
                path=path,
                rule="control_chars",
                severity="error",
                detail=f"{name} control character appears {count} time(s)",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 2. latex_delimiters (error) — not inside fenced code blocks
# ---------------------------------------------------------------------------

_LATEX_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\\\[", r"\["),
    (r"\\\]", r"\]"),
    (r"\\\(", r"\("),
    (r"\\\)", r"\)"),
    (r"\\begin\{equation\}", r"\begin{equation}"),
    (r"\\section\{", r"\section{"),
    (r"\\subsection\{", r"\subsection{"),
    (r"\\textbf\{", r"\textbf{"),
)
_LATEX_COMPILED = tuple(
    (re.compile(pattern), label) for pattern, label in _LATEX_PATTERNS
)


def _check_latex_delimiters(path: Path, code_free_text: str) -> list[Finding]:
    findings: list[Finding] = []
    for regex, label in _LATEX_COMPILED:
        matches = list(regex.finditer(code_free_text))
        if not matches:
            continue
        line = _line_of(code_free_text, matches[0].start())
        findings.append(
            Finding(
                path=path,
                rule="latex_delimiters",
                severity="error",
                detail=(
                    f"leftover LaTeX markup '{label}' ({len(matches)} occurrence(s))"
                ),
                line=line,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 3. unbalanced_math (error) — ignoring fenced code blocks
# ---------------------------------------------------------------------------

_BEGIN_RE = re.compile(r"\\begin\{(\w+)\}")
_END_RE = re.compile(r"\\end\{(\w+)\}")


def _check_unbalanced_math(path: Path, code_free_text: str) -> list[Finding]:
    findings: list[Finding] = []

    dollar_count = code_free_text.count("$$")
    if dollar_count % 2 == 1:
        line = _line_of(code_free_text, code_free_text.index("$$"))
        findings.append(
            Finding(
                path=path,
                rule="unbalanced_math",
                severity="error",
                detail=f"odd number of '$$' delimiters ({dollar_count})",
                line=line,
            )
        )

    begins = Counter(_BEGIN_RE.findall(code_free_text))
    ends = Counter(_END_RE.findall(code_free_text))
    for name in sorted(set(begins) | set(ends)):
        begin_count, end_count = begins.get(name, 0), ends.get(name, 0)
        if begin_count != end_count:
            first = re.search(
                rf"\\(?:begin|end)\{{{re.escape(name)}\}}", code_free_text
            )
            line = _line_of(code_free_text, first.start()) if first else None
            findings.append(
                Finding(
                    path=path,
                    rule="unbalanced_math",
                    severity="error",
                    detail=(
                        f"\\begin{{{name}}} ({begin_count}) does not match "
                        f"\\end{{{name}}} ({end_count})"
                    ),
                    line=line,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 4. scrambled_text (warning) — reuses converter.pdf_ocr.is_scrambled_text
# ---------------------------------------------------------------------------

_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)", re.MULTILINE)


def _check_scrambled_text(path: Path, text: str) -> list[Finding]:
    matches = list(_PAGE_HEADING_RE.finditer(text))
    if not matches:
        return []

    findings: list[Finding] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end]
        if is_scrambled_text(section):
            page_number = int(match.group(1))
            findings.append(
                Finding(
                    path=path,
                    rule="scrambled_text",
                    severity="warning",
                    detail=f"page {page_number} text looks scrambled",
                    line=_line_of(text, match.start()),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 5. orphan_accents (warning)
# ---------------------------------------------------------------------------

# Same spacing-accent glyphs twomarkdown.converter.clean already knows to
# repair pre-write (acute, tilde, diaeresis, circumflex, caron, ring). Seeing
# any of these still sitting next to a bare letter means repair either did
# not run or missed a case.
_ACCENT_CHARS = "\u00b4\u02dc\u00a8\u02c6\u02c7\u02da"  # ´ ˜ ¨ ˆ ˇ ˚
_ORPHAN_ACCENT_RE = re.compile(f"[A-Za-z][{_ACCENT_CHARS}]|[{_ACCENT_CHARS}][A-Za-z]")


def _check_orphan_accents(path: Path, text: str) -> list[Finding]:
    matches = list(_ORPHAN_ACCENT_RE.finditer(text))
    if not matches:
        return []
    sample = matches[0].group(0)
    return [
        Finding(
            path=path,
            rule="orphan_accents",
            severity="warning",
            detail=(
                f"{len(matches)} orphan accent glyph(s) next to a letter "
                f"(e.g. '{sample}')"
            ),
            line=_line_of(text, matches[0].start()),
        )
    ]


# ---------------------------------------------------------------------------
# 6. empty_output (warning)
# ---------------------------------------------------------------------------

EMPTY_OUTPUT_MIN_CHARS = 20


def _check_empty_output(path: Path, text: str) -> list[Finding]:
    _, body = _split_frontmatter(text)
    stripped = body.strip()
    if len(stripped) < EMPTY_OUTPUT_MIN_CHARS:
        return [
            Finding(
                path=path,
                rule="empty_output",
                severity="warning",
                detail=f"body has only {len(stripped)} character(s) after frontmatter",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# 7. missing_figure_assets (warning)
# ---------------------------------------------------------------------------

_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _check_missing_figure_assets(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    base_dir = path.parent
    for match in _IMAGE_LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        # A markdown title is quoted: (path "some title"). Splitting on any space
        # would truncate the many legitimate asset paths that contain one, e.g.
        # "Tema 10_assets/Tema 10-fig-p1-1.png".
        for quote in ('"', "'"):
            head, sep, _ = target.partition(f" {quote}")
            if sep:
                target = head
                break
        target = target.strip().strip("<>")
        if target.startswith(("http://", "https://", "data:")):
            continue
        try:
            exists = (base_dir / target).exists()
        except (OSError, ValueError):
            continue
        if not exists:
            findings.append(
                Finding(
                    path=path,
                    rule="missing_figure_assets",
                    severity="warning",
                    detail=f"image target '{target}' does not exist",
                    line=_line_of(text, match.start()),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 8. language_mismatch (warning) — reuses twomarkdown.language.guess_language
# ---------------------------------------------------------------------------

_FRONTMATTER_LANGUAGE_RE = re.compile(
    r'^language:\s*"?([A-Za-z]{2})"?\s*$', re.MULTILINE
)
_FIGURE_CAPTION_RE = re.compile(r"^> \*\*Figura.*$", re.MULTILINE)


def _check_language_mismatch(path: Path, text: str) -> list[Finding]:
    frontmatter, _ = _split_frontmatter(text)
    if not frontmatter:
        return []
    lang_match = _FRONTMATTER_LANGUAGE_RE.search(frontmatter)
    if not lang_match:
        return []
    declared = lang_match.group(1).lower()

    findings: list[Finding] = []
    for match in _FIGURE_CAPTION_RE.finditer(text):
        caption = match.group(0)
        guessed = guess_language(caption)
        if guessed and guessed != declared:
            findings.append(
                Finding(
                    path=path,
                    rule="language_mismatch",
                    severity="warning",
                    detail=(
                        f"frontmatter declares language '{declared}' but a figure "
                        f"caption looks like '{guessed}'"
                    ),
                    line=_line_of(text, match.start()),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_markdown(path: Path, text: str) -> list[Finding]:
    """Run every deterministic rule over one file's already-read text.

    Never raises: a rule that hits unexpected input reports a finding for
    itself instead of propagating, so one malformed file cannot abort a
    batch of thousands.
    """
    text = text or ""
    code_free = _strip_fenced_code(text)

    findings: list[Finding] = []
    findings += _safe(_check_control_chars, path, text)
    findings += _safe(_check_latex_delimiters, path, code_free)
    findings += _safe(_check_unbalanced_math, path, code_free)
    findings += _safe(_check_scrambled_text, path, text)
    findings += _safe(_check_orphan_accents, path, text)
    findings += _safe(_check_empty_output, path, text)
    findings += _safe(_check_missing_figure_assets, path, text)
    findings += _safe(_check_language_mismatch, path, text)
    return _sorted(findings)


_SKIP_FILENAME_PREFIXES = ("2markdown-report",)


def _is_skipped(rel_path: Path) -> bool:
    if any(part.startswith(".") for part in rel_path.parts):
        return True
    return rel_path.name.startswith(_SKIP_FILENAME_PREFIXES)


def validate_tree(root: Path) -> list[Finding]:
    """Walk a ``*_2markdown`` output directory and validate every ``.md`` file.

    Dotfiles and dot-directories (``.2markdown-ocr-cache``, ``.unzipped``,
    ``.2markdown-manifest.json``, ...) are skipped, as are the generated
    ``2markdown-report.*`` files. A file that cannot be read is reported as a
    finding rather than raised, so one bad file never aborts the walk.
    """
    root = Path(root)
    if not root.exists():
        return []

    findings: list[Finding] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _is_skipped(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                Finding(
                    path=path,
                    rule="unreadable_file",
                    severity="error",
                    detail=f"could not read file: {exc}",
                )
            )
            continue
        findings.extend(validate_markdown(path, text))
    return _sorted(findings)


def format_report(findings: Iterable[Finding]) -> str:
    """A short, plain-text, grouped summary — no colour codes."""
    findings = _sorted(list(findings))
    if not findings:
        return "No issues found."

    by_path: dict[Path, list[Finding]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)

    lines: list[str] = []
    for path in sorted(by_path, key=str):
        lines.append(str(path))
        for finding in by_path[path]:
            location = f" (line {finding.line})" if finding.line is not None else ""
            lines.append(
                f"  [{finding.severity}] {finding.rule}{location}: {finding.detail}"
            )

    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    lines.append("")
    lines.append(
        f"{len(findings)} finding(s) in {len(by_path)} file(s): "
        f"{error_count} error(s), {warning_count} warning(s)"
    )
    for rule, count in sorted(Counter(f.rule for f in findings).items()):
        lines.append(f"  {rule}: {count}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: validate a converted output tree. Exit 1 when errors are found."""
    import argparse

    parser = argparse.ArgumentParser(description="Validate converted markdown.")
    parser.add_argument("root", type=Path, help="a *_2markdown output directory")
    parser.add_argument(
        "--warnings-fail",
        action="store_true",
        help="exit non-zero on warnings too, not just errors",
    )
    args = parser.parse_args(argv)

    findings = validate_tree(args.root)
    print(format_report(findings))

    errors = sum(1 for f in findings if f.severity == "error")
    if errors:
        return 1
    if args.warnings_fail and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
