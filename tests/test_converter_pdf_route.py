"""Unit tests for pdf-inspector page routing (no real PDF corpus)."""

from __future__ import annotations

import fitz

from twomarkdown.converter.pdf_route import (
    PageMarkdownInfo,
    PagesExtractionSnapshot,
    native_markdown_by_page,
    pixmap_has_ruled_table_grid,
    route_pages,
)


def _snapshot(
    pages: list[tuple[int, str, bool]],
    tables: tuple[int, ...] = (),
) -> PagesExtractionSnapshot:
    return PagesExtractionSnapshot(
        pages=[
            PageMarkdownInfo(page=index, markdown=md, needs_ocr=needs)
            for index, md, needs in pages
        ],
        pages_with_tables=tables,
    )


class TestRoutePages:
    def test_native_page_needs_no_ocr(self) -> None:
        snap = _snapshot([(0, "Hello world with enough native text.", False)])
        routes = route_pages(snap)
        assert routes == {1: "native"}

    def test_table_page_with_ocr_goes_to_tesseract(self) -> None:
        snap = _snapshot([(0, "", True)], tables=(1,))
        routes = route_pages(snap, table_pages_use_tesseract=True)
        assert routes[1] == "tesseract"

    def test_table_flag_off_sends_table_page_to_vision(self) -> None:
        snap = _snapshot([(0, "", True)], tables=(1,))
        routes = route_pages(snap, table_pages_use_tesseract=False)
        assert routes[1] == "vision"

    def test_non_table_scanned_page_is_vision(self) -> None:
        snap = _snapshot([(0, "", True)])
        routes = route_pages(snap)
        assert routes[1] == "vision"

    def test_force_ocr_overrides_native(self) -> None:
        snap = _snapshot([(0, "native text", False)])
        routes = route_pages(snap, force_ocr=True)
        assert routes[1] == "vision"

    def test_native_markdown_only_for_native_routes(self) -> None:
        snap = _snapshot(
            [
                (0, "Page one", False),
                (1, "", True),
            ],
            tables=(2,),
        )
        routes = route_pages(snap, table_pages_use_tesseract=True)
        native = native_markdown_by_page(snap, routes)
        assert native == {1: "Page one"}


class TestRuledGridDetector:
    def test_detects_line_grid_on_synthetic_pixmap(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=220, height=220)
        shape = page.new_shape()
        for y in range(30, 200, 35):
            shape.draw_line((15, y), (205, y))
        for x in range(30, 200, 35):
            shape.draw_line((x, 15), (x, 205))
        shape.finish(width=1.0, color=(0, 0, 0))
        shape.commit()
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        doc.close()
        assert pixmap_has_ruled_table_grid(pix)

    def test_plain_text_page_is_not_a_grid(self) -> None:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Solo texto sin rejilla de tabla.")
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        doc.close()
        assert not pixmap_has_ruled_table_grid(pix)
