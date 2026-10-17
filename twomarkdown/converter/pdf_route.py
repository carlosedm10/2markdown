"""Per-page PDF routing via pdf-inspector (native / Tesseract / vision)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from twomarkdown.config import pdf_ocr_config

logger = logging.getLogger(__name__)

PageRoute = Literal["native", "tesseract", "vision"]


@dataclass(frozen=True)
class PageMarkdownInfo:
    """One page from pdf-inspector, injectable in tests without the wheel."""

    page: int  # 0-indexed
    markdown: str
    needs_ocr: bool


@dataclass(frozen=True)
class PagesExtractionSnapshot:
    pages: list[PageMarkdownInfo]
    pages_with_tables: tuple[int, ...]  # 1-indexed


@dataclass(frozen=True)
class PdfInspection:
    snapshot: PagesExtractionSnapshot
    routes: dict[int, PageRoute]  # 1-indexed


def route_pages(
    snapshot: PagesExtractionSnapshot,
    *,
    force_ocr: bool = False,
    table_pages_use_tesseract: bool = True,
) -> dict[int, PageRoute]:
    """Pure routing from an inspector snapshot to per-page engines."""
    table_pages = set(snapshot.pages_with_tables)
    routes: dict[int, PageRoute] = {}
    for page in snapshot.pages:
        page_num = page.page + 1
        if force_ocr:
            routes[page_num] = "vision"
            continue
        if not page.needs_ocr:
            routes[page_num] = "native"
            continue
        if table_pages_use_tesseract and page_num in table_pages:
            routes[page_num] = "tesseract"
        else:
            routes[page_num] = "vision"
    return routes


def native_markdown_by_page(
    snapshot: PagesExtractionSnapshot, routes: dict[int, PageRoute]
) -> dict[int, str]:
    """Inspector markdown for pages routed as native (1-indexed)."""
    out: dict[int, str] = {}
    for page in snapshot.pages:
        page_num = page.page + 1
        if routes.get(page_num) != "native":
            continue
        text = (page.markdown or "").strip()
        if text:
            out[page_num] = text
    return out


def _snapshot_from_result(result: object) -> PagesExtractionSnapshot:
    pages = [
        PageMarkdownInfo(
            page=int(p.page),
            markdown=str(p.markdown or ""),
            needs_ocr=bool(p.needs_ocr),
        )
        for p in result.pages
    ]
    tables = tuple(int(p) for p in result.pages_with_tables)
    return PagesExtractionSnapshot(pages=pages, pages_with_tables=tables)


def inspect_pdf(pdf_path: Path | str) -> PdfInspection | None:
    """Call pdf-inspector once per PDF; None when import or extraction fails."""
    path = Path(pdf_path)
    try:
        import pdf_inspector
    except Exception as exc:
        logger.warning("pdf-inspector unavailable: %s", exc)
        return None
    try:
        result = pdf_inspector.extract_pages_markdown(str(path))
    except Exception as exc:
        logger.warning("pdf-inspector failed for %s: %s", path.name, exc)
        return None
    snapshot = _snapshot_from_result(result)
    routes = route_pages(
        snapshot,
        force_ocr=pdf_ocr_config.pdf_ocr_force,
        table_pages_use_tesseract=pdf_ocr_config.table_pages_use_tesseract,
    )
    return PdfInspection(snapshot=snapshot, routes=routes)


def effective_route(
    page_num: int,
    routes: dict[int, PageRoute] | None,
    *,
    pixmap: object | None = None,
) -> PageRoute | None:
    """Resolve the route for one page, optionally promoting vision → tesseract."""
    if routes is None:
        return None
    route = routes.get(page_num)
    if route != "vision" or pixmap is None:
        return route
    if pixmap_has_ruled_table_grid(pixmap):
        return "tesseract"
    return route


def pixmap_has_ruled_table_grid(pix: object) -> bool:
    """True when a rendered page shows a ruled-line table grid (scanned tables)."""
    try:
        width = int(pix.width)
        height = int(pix.height)
        stride = int(pix.stride)
        samples = pix.samples
    except Exception:
        return False
    if width < 40 or height < 40 or not samples:
        return False

    step = max(1, min(width, height) // 200)
    w_samples = (width + step - 1) // step
    h_samples = (height + step - 1) // step
    dark_threshold = 200
    min_line_ratio = 0.55
    min_lines_each_axis = 3

    row_dark = [0] * h_samples
    col_dark = [0] * w_samples
    for sy in range(h_samples):
        y = min(height - 1, sy * step)
        for sx in range(w_samples):
            x = min(width - 1, sx * step)
            offset = y * stride + x * 3
            if offset + 2 >= len(samples):
                continue
            r, g, b = samples[offset], samples[offset + 1], samples[offset + 2]
            if r < dark_threshold and g < dark_threshold and b < dark_threshold:
                row_dark[sy] += 1
                col_dark[sx] += 1

    row_hits = [
        i for i, count in enumerate(row_dark) if count >= w_samples * min_line_ratio
    ]
    col_hits = [
        i for i, count in enumerate(col_dark) if count >= h_samples * min_line_ratio
    ]
    if len(row_hits) < min_lines_each_axis or len(col_hits) < min_lines_each_axis:
        return False

    def _spread(indices: list[int]) -> bool:
        if len(indices) < min_lines_each_axis:
            return False
        span = indices[-1] - indices[0]
        return span >= max(2, (len(indices) - 1) * 2)

    return _spread(row_hits) and _spread(col_hits)


def count_routes(routes: dict[int, PageRoute]) -> tuple[int, int, int]:
    """Return (native, tesseract, vision) page counts."""
    native = tesseract = vision = 0
    for route in routes.values():
        if route == "native":
            native += 1
        elif route == "tesseract":
            tesseract += 1
        else:
            vision += 1
    return native, tesseract, vision
