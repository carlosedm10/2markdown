class TestMathsIsNotProse:
    def test_spanish_maths_page_is_not_called_english(self) -> None:
        """guess_language() — LaTeX subscripts must not outvote the prose.

        "a_n", "a_1" tokenise as the English article "a", and \\in / \\to collide
        with English stopwords; a Spanish maths page reported as English.
        """
        from twomarkdown.language import guess_language

        text = (
            "## Tema 6: Sucesiones y Series\n\n"
            "**Sucesión:** secuencia infinita de números numerados. "
            "Término general para el caso en que no se puede calcular "
            "$\\{a_n\\}_{n=1}^{\\infty}$ donde $a_n \\in \\mathbb{R}$.\n"
            "- Sucesión de números reales $\\{a_n\\}$ con $a_n \\in \\mathbb{R}$.\n"
            "- Progresión aritmética $a_n = a_{n-1} + d$ con $n \\to \\infty$.\n"
        )
        assert guess_language(text) == "es"

    def test_english_prose_still_detected(self) -> None:
        """guess_language() — stripping maths does not break ordinary English."""
        from twomarkdown.language import guess_language

        text = (
            "This is a sequence of real numbers and the general term is "
            "defined by a recurrence that we use to compute the next value "
            "in the series for any n that is greater than one."
        )
        assert guess_language(text) == "en"

    def test_strip_non_prose_removes_maths_and_code(self) -> None:
        """strip_non_prose() — maths, LaTeX commands and fences are dropped."""
        from twomarkdown.language import strip_non_prose

        out = strip_non_prose("hola $a_n \\in R$ mundo\n```\ncode a to in\n```\n")
        assert "hola" in out and "mundo" in out
        assert "a_n" not in out and "code" not in out
