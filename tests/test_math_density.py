"""Unit tests for converter/math_density.py's deterministic math detector."""

from __future__ import annotations

from twomarkdown.converter.math_density import analyze, find_math_spans, math_density

SAMPLE_TEXT = (
    "Formulario Métodos Estadísticos en Economía (ADE) Inferencia para medias: "
    "procedimientos z para una muestra Estadístico z: z = ¯x −µ0 σ/√n Intervalo "
    "de confianza para µ: ¯x ± zα/2 · σ √n Inferencia para medias: procedimientos "
    "t para una muestra Estadístico t para una muestra: t = ¯x −µ0 s/√n n −1 "
    "grados de libertad Intervalo de confianza para µ: ¯x ± tα/2 n−1 · s √n "
    "Estadístico t de dos muestras: t = (x1 −x2) −(µ10 −µ20) q k = min{n1 −1, "
    "n2 −1} grados de libertad s2 1 n1 + s2 2 n2"
)

PLAIN_PROSE = (
    "El alumno debe presentar el trabajo final antes del viernes. La entrega se "
    "realiza a través del campus virtual y debe incluir una breve introduccion, "
    "el desarrollo del caso practico elegido y una conclusion personal sobre lo "
    "aprendido durante el curso de introduccion a la economia."
)


def test_sample_text_has_math_spans():
    spans = find_math_spans(SAMPLE_TEXT)
    assert spans, "expected at least one math-like span in the sample formulario"
    # A handful of the formulas should be found verbatim as spans.
    joined = " ".join(sp.text for sp in spans)
    assert "σ/√n" in joined or "s/√n" in joined


def test_sample_text_has_nonzero_density():
    stats = analyze(SAMPLE_TEXT)
    assert 0.0 < stats.math_density <= 1.0


def test_sample_text_detects_structure_lost():
    stats = analyze(SAMPLE_TEXT)
    assert stats.structure_lost is True


def test_plain_prose_has_no_spans():
    assert find_math_spans(PLAIN_PROSE) == []


def test_plain_prose_has_zero_density():
    assert math_density(PLAIN_PROSE) == 0.0


def test_empty_text_has_zero_density():
    assert math_density("") == 0.0


def test_dangling_sqrt_is_structure_lost():
    spans = find_math_spans("k = min{n1 −1, n2 −1} y √ n1")
    assert any(sp.structure_lost for sp in spans)
