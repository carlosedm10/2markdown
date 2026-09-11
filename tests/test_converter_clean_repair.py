"""PDF text-artifact repair: ligature control codes, math delimiters, accents."""

from twomarkdown.converter.clean import repair_pdf_text_artifacts


class TestRepairPdfTextArtifacts:
    def test_maps_ligature_control_codes(self) -> None:
        """repair_pdf_text_artifacts() — \\x1d/\\x1c become fl/fi."""
        assert (
            repair_pdf_text_artifacts("Control de \x1dujo, entrega \x1cable")
            == "Control de flujo, entrega fiable"
        )

    def test_maps_math_delimiter_control_codes(self) -> None:
        """repair_pdf_text_artifacts() — big brackets become ASCII brackets."""
        assert repair_pdf_text_artifacts("L-1\x14 1/s5 \x15") == "L-1[ 1/s5 ]"

    def test_strips_unmapped_control_characters(self) -> None:
        """repair_pdf_text_artifacts() — no control byte survives into markdown."""
        out = repair_pdf_text_artifacts("A\x02B\x07C")
        assert out == "ABC"
        assert not any(ord(ch) < 32 for ch in out)

    def test_composes_spacing_accents(self) -> None:
        """repair_pdf_text_artifacts() — LaTeX accents compose onto their letter."""
        assert repair_pdf_text_artifacts("M´odulo 2.- ´Algebra") == "Módulo 2.- Álgebra"
        assert repair_pdf_text_artifacts("tama˜no") == "tamaño"
        assert repair_pdf_text_artifacts("id´enticos") == "idénticos"

    def test_expands_presentation_ligatures(self) -> None:
        """repair_pdf_text_artifacts() — ﬁ/ﬂ presentation forms become plain letters."""
        assert repair_pdf_text_artifacts("m ﬁlas") == "m filas"
        assert repair_pdf_text_artifacts("V eﬀ") == "V eff"

    def test_leaves_code_fences_intact(self) -> None:
        """repair_pdf_text_artifacts() — backticks are not treated as grave accents."""
        assert repair_pdf_text_artifacts("```json\n{}\n```") == "```json\n{}\n```"

    def test_leaves_clean_text_unchanged(self) -> None:
        """repair_pdf_text_artifacts() — ordinary Spanish text is untouched."""
        text = "La función f es par, impar o ninguna. Ω = 1 μF, ≤ 5 %."
        assert repair_pdf_text_artifacts(text) == text
