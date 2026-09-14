"""Tests for language guess and YAML frontmatter."""

from pathlib import Path

from twomarkdown.frontmatter import build_frontmatter, refresh_frontmatter_after_edit
from twomarkdown.language import guess_language


class TestGuessLanguage:
    def test_guess_language_spanish(self) -> None:
        text = (
            "El informe de la empresa describe las medidas para el mercado "
            "y también explica por qué este plan es importante para los clientes "
            "en las regiones del sur. "
        ) * 3
        assert guess_language(text) == "es"

    def test_guess_language_english(self) -> None:
        text = (
            "The report from the company describes the measures for the market "
            "and this plan is important for the customers in the south region. "
        ) * 3
        assert guess_language(text) == "en"

    def test_guess_language_too_short(self) -> None:
        assert guess_language("hola") is None


class TestBuildFrontmatter:
    def test_build_frontmatter_includes_source_and_char_count(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "in" / "notes.txt"
        source.parent.mkdir()
        source.write_text("hello")
        markdown = "# Title here\n\nHello world\n\n### Table (page 1)\n\n| a | b |\n"
        yaml = build_frontmatter(source, tmp_path / "in", markdown)
        assert 'source: "notes.txt"' in yaml or "source: notes.txt" in yaml
        assert "title:" in yaml
        assert "Title here" in yaml
        assert "tables:" in yaml
        assert yaml.startswith("---")


class TestRefreshFrontmatterAfterEdit:
    """B5: a page-level write must not leave `char_count`/`converted_at`
    describing the pre-edit body."""

    def test_char_count_and_timestamp_are_updated(self) -> None:
        content = (
            "---\n"
            'source: "Tema 1.pdf"\n'
            'converted_at: "2026-09-13T13:22:27+00:00"\n'
            "char_count: 1624\n"
            "---\n\n"
            "## Page 1\n\nnew, longer body than before\n"
        )
        refreshed = refresh_frontmatter_after_edit(content)

        # Same split the implementation itself uses (frontmatter block vs.
        # body), so this assertion doesn't depend on guessing an exact
        # boundary/whitespace convention independently.
        end = content.find("\n---", 4)
        body = content[content.find("\n", end + 1) + 1 :]
        assert f"char_count: {len(body)}" in refreshed
        assert "char_count: 1624" not in refreshed
        assert '"2026-09-13T13:22:27+00:00"' not in refreshed
        assert "new, longer body than before" in refreshed

    def test_other_fields_are_left_alone(self) -> None:
        content = (
            "---\n"
            'source: "Tema 1.pdf"\n'
            'ocr_model: "openai:gpt-4o-mini"\n'
            'converted_at: "2026-09-13T13:22:27+00:00"\n'
            "char_count: 10\n"
            "---\n\nbody\n"
        )
        refreshed = refresh_frontmatter_after_edit(content)
        assert 'ocr_model: "openai:gpt-4o-mini"' in refreshed
        assert 'source: "Tema 1.pdf"' in refreshed

    def test_content_without_frontmatter_is_unchanged(self) -> None:
        content = "## Page 1\n\nno frontmatter here\n"
        assert refresh_frontmatter_after_edit(content) == content
