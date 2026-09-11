"""Figure region detection, rendering, and inline placement."""

from pathlib import Path

import fitz

from twomarkdown.converter.figures import (
    Figure,
    detect_figure_regions,
    extract_figures,
    figure_block,
    insert_figure_blocks,
)


def _pdf_with_vector_figure(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Respuesta en frecuencia")
    # A cluster of strokes in the lower half: this is the "figure".
    for i in range(12):
        x = 100 + i * 10
        page.draw_line(fitz.Point(x, 400), fitz.Point(x + 8, 340 + i * 4))
    page.draw_rect(fitz.Rect(100, 330, 260, 410))
    doc.save(path)
    doc.close()
    return path


def _pdf_text_only(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Sólo texto, sin figuras")
    doc.save(path)
    doc.close()
    return path


class TestDetectFigureRegions:
    def test_finds_a_vector_figure(self, tmp_path: Path) -> None:
        """detect_figure_regions() — a cluster of strokes is reported as a figure."""
        doc = fitz.open(_pdf_with_vector_figure(tmp_path / "fig.pdf"))
        regions = detect_figure_regions(doc[0])
        doc.close()
        assert len(regions) == 1
        assert regions[0][1] in {"vector", "mixed"}

    def test_ignores_a_text_only_page(self, tmp_path: Path) -> None:
        """detect_figure_regions() — prose alone yields no figures."""
        doc = fitz.open(_pdf_text_only(tmp_path / "text.pdf"))
        regions = detect_figure_regions(doc[0])
        doc.close()
        assert regions == []


class TestExtractFigures:
    def test_writes_png_per_region(self, tmp_path: Path) -> None:
        """extract_figures() — each region is rendered to its own PNG."""
        pdf = _pdf_with_vector_figure(tmp_path / "fig.pdf")
        figures = extract_figures(pdf, tmp_path / "assets")
        assert len(figures) == 1
        assert figures[0].path.is_file()
        assert figures[0].path.read_bytes().startswith(b"\x89PNG")
        assert figures[0].page_number == 1

    def test_never_raises_on_a_broken_pdf(self, tmp_path: Path) -> None:
        """extract_figures() — an unreadable PDF soft-fails to an empty list."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf")
        assert extract_figures(broken, tmp_path / "assets") == []


class TestInsertFigureBlocks:
    def test_places_block_inside_its_page_section(self) -> None:
        """insert_figure_blocks() — a figure lands under its own page heading."""
        md = "## Page 1\n\nuno\n\n## Page 2\n\ndos\n"
        out = insert_figure_blocks(md, {1: ["FIG-ONE"]})
        assert out.index("FIG-ONE") < out.index("## Page 2")

    def test_appends_when_there_are_no_page_headings(self) -> None:
        """insert_figure_blocks() — without page headings, blocks go at the end."""
        out = insert_figure_blocks("cuerpo\n", {3: ["FIG"]})
        assert out.rstrip().endswith("FIG")

    def test_no_figures_leaves_markdown_untouched(self) -> None:
        """insert_figure_blocks() — an empty mapping is a no-op."""
        md = "## Page 1\n\nuno\n"
        assert insert_figure_blocks(md, {}) == md


class TestFigureBlock:
    def test_renders_relative_link_and_description(self, tmp_path: Path) -> None:
        """figure_block() — links the crop relative to the output root."""
        figure = Figure(
            page_number=4,
            index=2,
            path=tmp_path / "doc_assets" / "doc-fig-p4-2.png",
            rect=(0.0, 0.0, 10.0, 10.0),
            kind="vector",
        )
        block = figure_block(figure, "Un circuito RC.", output_root=tmp_path)
        assert "### Figura 4.2" in block
        assert "(doc_assets/doc-fig-p4-2.png)" in block
        assert "> **Figura (descripción generada):** Un circuito RC." in block

    def test_omits_quote_without_description(self, tmp_path: Path) -> None:
        """figure_block() — no description means no blockquote."""
        figure = Figure(
            page_number=1,
            index=1,
            path=tmp_path / "a_assets" / "a-fig-p1-1.png",
            rect=(0.0, 0.0, 10.0, 10.0),
            kind="raster",
        )
        assert "descripción generada" not in figure_block(
            figure, "", output_root=tmp_path
        )


class TestTextHeavyRegions:
    def test_rejects_a_bordered_prose_box(self, tmp_path: Path) -> None:
        """detect_figure_regions() — a box full of sentences is not a figure."""
        path = tmp_path / "box.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.draw_rect(fitz.Rect(60, 300, 520, 460))
        for i in range(8):
            page.draw_line(fitz.Point(60, 300 + i), fitz.Point(520, 300 + i))
        body = (
            "La matriz con todos sus elementos nulos se llama matriz nula. "
            "La matriz opuesta de A es la matriz con los elementos cambiados de signo. "
            "Sean A y B matrices de tamano m x n y lambda un elemento de K. Pruebe: "
        )
        page.insert_textbox(fitz.Rect(70, 310, 510, 450), body, fontsize=9)
        doc.save(path)
        doc.close()

        opened = fitz.open(path)
        regions = detect_figure_regions(opened[0])
        opened.close()
        assert regions == []

    def test_rejects_full_width_theme_bands(self, tmp_path: Path) -> None:
        """detect_figure_regions() — full-width slide-theme bands are not figures."""
        path = tmp_path / "bands.pdf"
        doc = fitz.open()
        page = doc.new_page()
        width = page.rect.width
        # Beamer-style blocks: a few bands spanning almost the whole page.
        for top in (200, 260, 320, 380, 440, 500):
            page.draw_rect(fitz.Rect(20, top, width - 20, top + 40), fill=(0.9, 0.9, 1))
        doc.save(path)
        doc.close()

        opened = fitz.open(path)
        regions = detect_figure_regions(opened[0])
        opened.close()
        assert regions == []


class TestDescriptionsAreOptIn:
    def test_descriptions_are_off_by_default(self) -> None:
        """FigureConfig — captions are opt-in; they dominated measured runtime."""
        from twomarkdown.config import FigureConfig

        assert FigureConfig().describe_figures_llm is False

    def test_figures_rendered_without_captions(self, tmp_path: Path) -> None:
        """extract_figures() — crops are produced regardless of captioning."""
        pdf = _pdf_with_vector_figure(tmp_path / "fig.pdf")
        figures = extract_figures(pdf, tmp_path / "assets")
        assert figures and figures[0].path.is_file()
