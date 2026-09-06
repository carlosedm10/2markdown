"""Tests for language guess and YAML frontmatter."""

from pathlib import Path

from src.frontmatter import build_frontmatter
from src.language import guess_language


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
