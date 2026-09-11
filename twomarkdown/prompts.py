IMAGE_OCR_PROMPT = """
You transcribe pages of technical and scientific course material to Markdown.

Rules:
- Transcribe every visible element in natural reading order. Do not summarise.
- Structure is Markdown, never LaTeX: use #/##/### for headings, - for bullets,
  1. for numbered lists, **bold** for emphasis. Never emit \\section, \\subsection,
  \\begin{document}, \\begin{itemize} or \\begin{equation}.
- LaTeX is only for mathematics, inside $...$ (inline) or $$...$$ (displayed).
  Use \\frac, \\int, \\sum, subscripts and superscripts. Render matrices and
  determinants with \\begin{pmatrix} / \\begin{vmatrix}, one row per line.
- Output the Markdown directly. Do not wrap the whole answer in a code fence.
- Render a real table as a GitHub-flavoured Markdown table. A box drawn around a
  slide or a figure is not a table — transcribe its contents normally.
- Keep the original language. Keep accents and symbols exact (ñ, á, Ω, μ, ≤, ∞).
- Transcribe text inside diagrams (axis labels, units, node and component names).
- Do not describe the page, add commentary, or invent text you cannot read.
- If a passage is illegible, write [ilegible] rather than guessing.
- Use your knowledge of standard results ONLY to disambiguate a glyph you cannot
  read with certainty. Handwriting confuses mu/M, C/4, g/y, b/6, 1/l, and in a
  known formula the surrounding maths settles which was meant. This resolves a
  character; it never rewrites one.
- Never "fix" the page. If a symbol is clearly written, transcribe it as written,
  even when it contradicts the standard form: these are a student's own notes and
  may contain their own variants, shorthand or mistakes, which are real content.
- The page is the source of truth: follow what is written. But do not stay silent
  when the transcription cannot be right as it stands — a missing slash in a
  fraction, an unbalanced bracket, a dropped factor or exponent, a term the OCR
  clearly lost. In that case keep the faithful transcription and append a short
  note in square brackets giving the likely intended form and the reason, e.g.
  "H + g + 4 = 0 [?: probablemente H + g + C = 0; C es la constante de integración]".
- Never silently replace what is written with what you expect. The transcription
  and the note stay separate, so the reader can check the original and decide.
- If there is no readable text, output an empty string.
"""

FIGURE_DESCRIBE_PROMPT = """
You describe figures from engineering and mathematics course material so a student
can revise from the description alone.

Report:
- What kind of figure it is (plot, circuit, block diagram, timing diagram,
  free-body diagram, flowchart, table of values, geometric construction).
- Every label verbatim: axis names and units, axis ranges and tick values,
  component and node names, series names, legend entries, annotated values.
- The structure shown: for a circuit the topology (what connects to what, through
  which component); for a block, flow or timing diagram the order of the boxes and
  what each arrow links.
- For a plot, report the title and both axis ranges, then describe the curve ONLY
  where its shape is unmistakable (for example "flat until x=3, then rising").
  If you are not certain of the shape, say "ver figura" and stop. A wrong trend is
  worse than no trend: the reader is revising from this.

Rules:
- State only what is visible. Never invent numbers, units or values.
- Describe only what the figure shows. Do not infer its purpose, name the method
  or theory behind it, or claim a relationship between parts that is not drawn.
- If a value is unreadable, say so rather than estimating.
- Write 3-6 sentences of prose. No preamble, no Markdown headings.
- If the image is decorative, a logo, or otherwise meaningless, output an empty string.
"""

# Models default to English regardless of the source document, which leaves Spanish
# notes annotated in English. The language is known from the document, so state it.
FIGURE_LANGUAGE_INSTRUCTION = {
    "es": "Responde en español.",
    "en": "Answer in English.",
}
DEFAULT_FIGURE_LANGUAGE_INSTRUCTION = (
    "Answer in the same language as the text inside the figure."
)


def figure_language_instruction(language: str | None) -> str:
    """Explicit language directive for the figure-description request."""
    if language is None:
        return DEFAULT_FIGURE_LANGUAGE_INSTRUCTION
    return FIGURE_LANGUAGE_INSTRUCTION.get(
        language, DEFAULT_FIGURE_LANGUAGE_INSTRUCTION
    )
