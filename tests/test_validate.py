"""Deterministic, model-free markdown quality gate (twomarkdown.validate)."""

from pathlib import Path

from twomarkdown.validate import (
    Finding,
    format_report,
    validate_markdown,
    validate_tree,
)

CLEAN_SPANISH_MARKDOWN = """---
source: "informe.pdf"
language: "es"
---

# Informe trimestral

El equipo revisó los resultados del trimestre y confirmó que el plan sigue
en marcha para todas las regiones del sur, con especial atención a los
clientes que renovaron su contrato este año.

La ecuación del modelo es $x^2 + 1$ y describe el comportamiento esperado.

```latex
\\[ \\text{E/S} \\leftrightarrow \\text{CPU} \\]
\\section{Esto no debe disparar nada}
```

Fin del informe.
"""


def _findings_for_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


class TestControlChars:
    """validate_markdown() — control_chars fires on stray C0 bytes, especially NUL."""

    def test_fires_on_embedded_nul_byte(self) -> None:
        """A stray NUL (git sees a binary-looking .md) is reported with a count."""
        text = "# Title\n\nBody with a\x00stray null\x00here.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "control_chars"
        )
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "NUL" in findings[0].detail
        assert "2 time(s)" in findings[0].detail

    def test_silent_on_ordinary_text_with_newlines_and_tabs(self) -> None:
        """Newlines and tabs are allowed C0 characters and must not fire."""
        text = "# Title\n\n\tIndented body\nwith a normal line.\n"
        assert (
            _findings_for_rule(validate_markdown(Path("doc.md"), text), "control_chars")
            == []
        )


class TestLatexDelimiters:
    """validate_markdown() — latex_delimiters fires on leftover LaTeX escapes."""

    def test_fires_on_leftover_section_command(self) -> None:
        """A leftover \\section{...} renders as raw text, not a heading."""
        text = "# Doc\n\n\\section{Tema 3}\n\nBody text.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "latex_delimiters"
        )
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "\\section{" in findings[0].detail

    def test_does_not_fire_inside_fenced_code_block(self) -> None:
        """The same escapes are legitimate content inside a fenced code block."""
        text = (
            "# Doc\n\n"
            "```latex\n"
            "\\[ x \\]\n"
            "\\section{Tema 3}\n"
            "\\textbf{bold}\n"
            "```\n\n"
            "Ordinary body text.\n"
        )
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "latex_delimiters"
            )
            == []
        )

    def test_silent_on_clean_markdown(self) -> None:
        """Ordinary Spanish markdown with legitimate $ math has no leftover LaTeX."""
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), CLEAN_SPANISH_MARKDOWN),
            "latex_delimiters",
        )
        assert findings == []


class TestUnbalancedMath:
    """validate_markdown() — unbalanced_math fires on odd $$ or mismatched envs."""

    def test_fires_on_odd_dollar_dollar_count(self) -> None:
        """An unclosed $$ block leaves an odd number of $$ delimiters."""
        text = "# Doc\n\n$$x^2 + 1$$\n\n$$y = mx + b\n\nMore text.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "unbalanced_math"
        )
        assert any("$$" in f.detail for f in findings)
        assert all(f.severity == "error" for f in findings)

    def test_fires_on_unclosed_pmatrix_environment(self) -> None:
        """\\begin{pmatrix} with no matching \\end{pmatrix} is a real regression."""
        # No closing \end{pmatrix}, simulating a truncated matrix.
        text = "# Doc\n\n$$\\begin{pmatrix} a & b \\\\ c & d\n$$\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "unbalanced_math"
        )
        assert any("pmatrix" in f.detail for f in findings)

    def test_does_not_fire_inside_fenced_code_block(self) -> None:
        """A lone $$ or unmatched \\begin{} in a code fence is legitimate content."""
        text = (
            "# Doc\n\n"
            "```text\n"
            "$$ this is just one delimiter shown as an example\n"
            "\\begin{pmatrix} a & b \\end{pmatrix} plus an extra \\begin{cases}\n"
            "```\n\n"
            "Ordinary body text with balanced $$math$$ here.\n"
        )
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "unbalanced_math"
            )
            == []
        )

    def test_silent_on_clean_markdown(self) -> None:
        """Ordinary Spanish markdown with a single inline $...$ has balanced math."""
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), CLEAN_SPANISH_MARKDOWN), "unbalanced_math"
        )
        assert findings == []


class TestScrambledText:
    """validate_markdown() — scrambled_text warns when a page's text is shredded."""

    def test_fires_on_shredded_page_section(self) -> None:
        """A page whose text is mostly single-character tokens is unreadable."""
        shredded = " ".join(["x"] * 20 + ["Tol"] * 5)
        text = f"# Doc\n\n## Page 1\n\n{shredded}\n\n## Page 2\n\nTexto normal aquí.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "scrambled_text"
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "page 1" in findings[0].detail

    def test_silent_on_readable_page_section(self) -> None:
        """A normal prose page must not be flagged as scrambled."""
        text = (
            "# Doc\n\n## Page 1\n\n"
            "El equipo revisó los resultados del trimestre y confirmó el plan "
            "para las próximas semanas de trabajo en el proyecto.\n"
        )
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "scrambled_text"
            )
            == []
        )


class TestOrphanAccents:
    """validate_markdown() — orphan_accents warns on uncomposed spacing accents."""

    def test_fires_on_uncomposed_tilde(self) -> None:
        """'tama˜no' is a spacing tilde next to a letter, not a composed ñ."""
        text = "# Doc\n\nEl tama˜no del archivo es correcto.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "orphan_accents"
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_fires_on_uncomposed_acute(self) -> None:
        """'M´odulo' is a spacing acute accent next to a letter, not a composed ó."""
        text = "# Doc\n\nEl M´odulo 2 explica el tema.\n"
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "orphan_accents"
        )
        assert len(findings) == 1

    def test_silent_on_properly_composed_accents(self) -> None:
        """'tamaño' with a properly composed ñ must not be flagged."""
        text = "# Doc\n\nEl tamaño del módulo es correcto y también válido.\n"
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "orphan_accents"
            )
            == []
        )


class TestEmptyOutput:
    """validate_markdown() — empty_output warns when the body is too short."""

    def test_fires_on_near_empty_body(self) -> None:
        """A body of only a couple of characters after frontmatter is a failed page."""
        text = '---\nsource: "x.pdf"\n---\n\nHi\n'
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "empty_output"
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_silent_on_substantial_body(self) -> None:
        """A normal-length body must not be flagged as empty."""
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), CLEAN_SPANISH_MARKDOWN), "empty_output"
        )
        assert findings == []


class TestMissingFigureAssets:
    """validate_markdown() — missing_figure_assets warns on a dead image link."""

    def test_fires_when_image_target_does_not_exist(self, tmp_path: Path) -> None:
        """A ![...](path) pointing at a file never written is a broken figure link."""
        md_path = tmp_path / "doc.md"
        text = "# Doc\n\n![Figura 1](figures/missing.png)\n"
        findings = _findings_for_rule(
            validate_markdown(md_path, text), "missing_figure_assets"
        )
        assert len(findings) == 1
        assert "missing.png" in findings[0].detail
        assert findings[0].severity == "warning"

    def test_silent_when_image_target_exists(self, tmp_path: Path) -> None:
        """An image link that resolves to a real file next to the .md is fine."""
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "present.png").write_bytes(b"\x89PNG\r\n")
        md_path = tmp_path / "doc.md"
        text = "# Doc\n\n![Figura 1](figures/present.png)\n"
        assert (
            _findings_for_rule(
                validate_markdown(md_path, text), "missing_figure_assets"
            )
            == []
        )

    def test_silent_on_remote_image_link(self, tmp_path: Path) -> None:
        """http(s) image links are never checked against the local filesystem."""
        md_path = tmp_path / "doc.md"
        text = "# Doc\n\n![Remote](https://example.com/missing.png)\n"
        assert (
            _findings_for_rule(
                validate_markdown(md_path, text), "missing_figure_assets"
            )
            == []
        )


ENGLISH_FIGURE_CAPTION = (
    "> **Figura (descripción generada):** The diagram shows the relationship "
    "between the input and the output of the system, and how the data flows "
    "through each of the modules in the pipeline for this particular example."
)

SPANISH_FIGURE_CAPTION = (
    "> **Figura (descripción generada):** El diagrama muestra la relación "
    "entre la entrada y la salida del sistema, y cómo los datos fluyen por "
    "cada uno de los módulos en el proceso para este ejemplo en particular."
)


class TestLanguageMismatch:
    """validate_markdown() — language_mismatch warns on an off-language caption."""

    def test_fires_when_declared_spanish_but_caption_is_english(self) -> None:
        """frontmatter says es, but the generated figure caption reads as English."""
        text = f'---\nlanguage: "es"\n---\n\n# Doc\n\n{ENGLISH_FIGURE_CAPTION}\n'
        findings = _findings_for_rule(
            validate_markdown(Path("doc.md"), text), "language_mismatch"
        )
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "'es'" in findings[0].detail and "'en'" in findings[0].detail

    def test_silent_when_caption_matches_declared_language(self) -> None:
        """A Spanish caption under a Spanish frontmatter declaration is fine."""
        text = f'---\nlanguage: "es"\n---\n\n# Doc\n\n{SPANISH_FIGURE_CAPTION}\n'
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "language_mismatch"
            )
            == []
        )

    def test_silent_without_frontmatter_language(self) -> None:
        """No declared language means nothing to compare the caption against."""
        text = f"# Doc\n\n{ENGLISH_FIGURE_CAPTION}\n"
        assert (
            _findings_for_rule(
                validate_markdown(Path("doc.md"), text), "language_mismatch"
            )
            == []
        )


class TestValidateMarkdownClean:
    """validate_markdown() — a clean, ordinary file produces no findings at all."""

    def test_clean_spanish_markdown_is_silent(self) -> None:
        """Legitimate $ math and a legitimate fenced code block raise nothing."""
        assert validate_markdown(Path("doc.md"), CLEAN_SPANISH_MARKDOWN) == []

    def test_results_are_sorted_and_deterministic(self) -> None:
        """Running twice over the same broken text yields the same ordered list."""
        text = "# Doc\n\n\\section{A}\n\n$$unbalanced\n\nEl tama˜no es raro.\n"
        first = validate_markdown(Path("doc.md"), text)
        second = validate_markdown(Path("doc.md"), text)
        assert first == second
        assert first == sorted(
            first,
            key=lambda f: (
                str(f.path),
                f.rule,
                f.line if f.line is not None else -1,
                f.detail,
            ),
        )


class TestValidateTree:
    """validate_tree() — walks a *_2markdown output directory."""

    def test_walks_and_validates_md_files(self, tmp_path: Path) -> None:
        """A tree with one broken and one clean file reports only the broken one."""
        (tmp_path / "broken.md").write_text("# Doc\n\n\\section{Oops}\n")
        (tmp_path / "clean.md").write_text(CLEAN_SPANISH_MARKDOWN)
        findings = validate_tree(tmp_path)
        paths = {f.path for f in findings}
        assert tmp_path / "broken.md" in paths
        assert tmp_path / "clean.md" not in paths

    def test_skips_dot_directories_and_dotfiles(self, tmp_path: Path) -> None:
        """.2markdown-ocr-cache, .unzipped and dotfiles are never validated."""
        cache_dir = tmp_path / ".2markdown-ocr-cache"
        cache_dir.mkdir()
        (cache_dir / "page1.md").write_text("\x00broken\x00")
        unzipped_dir = tmp_path / ".unzipped"
        unzipped_dir.mkdir()
        (unzipped_dir / "inner.md").write_text("\x00broken\x00")
        (tmp_path / ".hidden.md").write_text("\x00broken\x00")
        assert validate_tree(tmp_path) == []

    def test_skips_generated_report_files(self, tmp_path: Path) -> None:
        """2markdown-report.* siblings are not markdown output to validate."""
        # Not .md in practice, but guard the name prefix defensively anyway.
        (tmp_path / "2markdown-report.md").write_text("\x00broken\x00")
        assert validate_tree(tmp_path) == []

    def test_missing_root_returns_no_findings(self, tmp_path: Path) -> None:
        """A root that does not exist yet must not raise."""
        assert validate_tree(tmp_path / "does-not-exist") == []


class TestFormatReport:
    """format_report() — short, grouped, plain-text summary."""

    def test_empty_findings_reports_no_issues(self) -> None:
        """No findings at all reports a clean bill of health."""
        assert format_report([]) == "No issues found."

    def test_groups_by_path_and_counts_by_rule(self) -> None:
        """Findings are grouped under their file and summarized by rule."""
        findings = [
            Finding(
                Path("a.md"),
                "control_chars",
                "error",
                "NUL control character appears 1 time(s)",
            ),
            Finding(
                Path("a.md"),
                "empty_output",
                "warning",
                "body has only 2 character(s) after frontmatter",
            ),
            Finding(
                Path("b.md"), "orphan_accents", "warning", "1 orphan accent glyph(s)"
            ),
        ]
        report = format_report(findings)
        assert "a.md" in report
        assert "b.md" in report
        assert "[error] control_chars" in report
        assert "[warning] empty_output" in report
        assert "3 finding(s) in 2 file(s): 1 error(s), 2 warning(s)" in report
        assert "control_chars: 1" in report
        assert "empty_output: 1" in report
        assert "orphan_accents: 1" in report
        # No colour/ANSI escape codes.
        assert "\x1b[" not in report


class TestImagePathsWithSpaces:
    def test_path_containing_spaces_is_not_truncated(self, tmp_path) -> None:
        """validate_markdown() — an asset path with spaces resolves correctly."""
        assets = tmp_path / "Tema 10_assets"
        assets.mkdir()
        (assets / "Tema 10-fig-p1-1.png").write_bytes(b"\x89PNG")
        md = tmp_path / "Tema 10.md"
        text = "![Figura p1-1](Tema 10_assets/Tema 10-fig-p1-1.png)\n"
        md.write_text(text, encoding="utf-8")
        findings = [
            f for f in validate_markdown(md, text) if f.rule == "missing_figure_assets"
        ]
        assert findings == []

    def test_quoted_title_is_still_stripped(self, tmp_path) -> None:
        """validate_markdown() — a quoted markdown title is not part of the path."""
        (tmp_path / "fig.png").write_bytes(b"\x89PNG")
        md = tmp_path / "doc.md"
        text = '![alt](fig.png "un título")\n'
        md.write_text(text, encoding="utf-8")
        findings = [
            f for f in validate_markdown(md, text) if f.rule == "missing_figure_assets"
        ]
        assert findings == []

    def test_genuinely_missing_asset_still_reported(self, tmp_path) -> None:
        """validate_markdown() — a real broken link is still caught."""
        md = tmp_path / "doc.md"
        text = "![alt](Tema 10_assets/nope.png)\n"
        md.write_text(text, encoding="utf-8")
        findings = [
            f for f in validate_markdown(md, text) if f.rule == "missing_figure_assets"
        ]
        assert len(findings) == 1
