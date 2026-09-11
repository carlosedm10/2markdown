"""LaTeX emitted by vision models is normalised to Markdown-renderable math."""

from twomarkdown.converter.clean import normalize_latex_markup


class TestNormalizeLatexMarkup:
    def test_display_math_becomes_double_dollar(self) -> None:
        """normalize_latex_markup() — \\[ .. \\] renders as $$ .. $$."""
        src = r"\[ \text{E/S} \leftrightarrow \text{CPU} \]"
        want = r"$$\text{E/S} \leftrightarrow \text{CPU}$$"
        assert normalize_latex_markup(src) == want

    def test_inline_math_becomes_single_dollar(self) -> None:
        """normalize_latex_markup() — \\( .. \\) renders as $ .. $."""
        assert normalize_latex_markup(r"sea \( m \times n \) el orden") == (
            r"sea $m \times n$ el orden"
        )

    def test_multiple_display_blocks(self) -> None:
        """normalize_latex_markup() — each block converts independently."""
        src = r"\[ A \] y \[ B \]"
        assert normalize_latex_markup(src) == "$$A$$ y $$B$$"

    def test_equation_environment_becomes_display_math(self) -> None:
        """normalize_latex_markup() — \\begin{equation} becomes $$."""
        out = normalize_latex_markup(r"\begin{equation}x^2\end{equation}")
        assert "$$" in out and "\\begin{equation}" not in out

    def test_section_commands_become_headings(self) -> None:
        """normalize_latex_markup() — \\section/\\subsection become # and ##."""
        out = normalize_latex_markup("\\section{Tema 3}\n\\subsection{Von Neumann}")
        assert "# Tema 3" in out
        assert "## Von Neumann" in out

    def test_multiline_display_math_is_preserved(self) -> None:
        """normalize_latex_markup() — a matrix spanning lines still converts."""
        src = "\\[\n\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}\n\\]"
        out = normalize_latex_markup(src)
        assert out.startswith("$$") and out.endswith("$$")
        assert "pmatrix" in out

    def test_code_fences_are_untouched(self) -> None:
        """normalize_latex_markup() — backslash forms inside code stay literal."""
        src = "```latex\n\\[ x \\]\n```"
        assert normalize_latex_markup(src) == src

    def test_plain_markdown_is_unchanged(self) -> None:
        """normalize_latex_markup() — ordinary text and $ math pass through."""
        src = "La funcion $f(x)$ es par.\n\n$$x^2 + 1$$\n"
        assert normalize_latex_markup(src) == src
