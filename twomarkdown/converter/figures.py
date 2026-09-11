"""Locate figures on a PDF page and render them as standalone images.

Engineering slides draw most of their figures — plots, circuits, block and timing
diagrams — as *vector* content, so ``page.get_images()`` returns nothing for them
and the text layer keeps only loose axis ticks and labels. This module finds both
raster and vector figure regions, merges them into blocks, and renders each block
from the page itself, which also keeps vector figures sharp at any DPI.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from twomarkdown.config import figure_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Figure:
    """A rendered figure region on a page."""

    page_number: int
    index: int
    path: Path
    rect: tuple[float, float, float, float]
    kind: str  # "vector" | "raster" | "mixed"


def _is_hairline(rect: fitz.Rect) -> bool:
    """Rules, underlines and table borders: long in one axis, flat in the other."""
    return rect.width < 4.0 or rect.height < 4.0


def _covers_page(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    page_area = page_rect.width * page_rect.height
    if page_area <= 0:
        return False
    return (rect.width * rect.height) / page_area > figure_config.figure_max_area_ratio


def _candidate_rects(
    page: fitz.Page,
) -> tuple[list[fitz.Rect], list[fitz.Rect], dict[tuple, str]]:
    """Return (vector_rects, raster_rects, rect->draw type) for figure detection."""
    page_rect = page.rect
    vector: list[fitz.Rect] = []
    raster: list[fitz.Rect] = []
    kinds: dict[tuple, str] = {}

    try:
        drawings = page.get_drawings()
    except Exception as exc:
        logger.debug("get_drawings failed: %s", exc)
        drawings = []
    for drawing in drawings:
        try:
            rect = fitz.Rect(drawing["rect"])
        except Exception:
            continue
        if rect.is_empty or rect.is_infinite:
            continue
        if _is_hairline(rect) or _covers_page(rect, page_rect):
            continue
        vector.append(rect)
        kinds[_rect_key(rect)] = str(drawing.get("type") or "")

    try:
        images = page.get_images(full=True)
    except Exception as exc:
        logger.debug("get_images failed: %s", exc)
        images = []
    for info in images:
        try:
            for rect in page.get_image_rects(info[0]):
                rect = fitz.Rect(rect)
                if rect.is_empty or _covers_page(rect, page_rect):
                    continue
                if (
                    rect.width < figure_config.figure_min_pt
                    or rect.height < figure_config.figure_min_pt
                ):
                    continue
                raster.append(rect)
        except Exception:
            continue

    return vector, raster, kinds


def _rect_key(rect: fitz.Rect) -> tuple:
    return (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))


def _merge_rects(rects: list[fitz.Rect], gap: float) -> list[fitz.Rect]:
    """Union rects that touch or sit within ``gap`` points of each other."""
    merged: list[fitz.Rect] = []
    for rect in rects:
        grown = fitz.Rect(rect)
        grown.x0 -= gap
        grown.y0 -= gap
        grown.x1 += gap
        grown.y1 += gap

        overlapping = [m for m in merged if m.intersects(grown)]
        if not overlapping:
            merged.append(fitz.Rect(rect))
            continue
        for m in overlapping:
            merged.remove(m)
        combined = fitz.Rect(rect)
        for m in overlapping:
            combined |= m
        merged.append(combined)

    # One pass can leave newly adjacent blocks unmerged; repeat until stable.
    if len(merged) != len(rects):
        again = _merge_rects(merged, gap)
        if len(again) != len(merged):
            return again
        return again
    return merged


def _text_metrics(page: fitz.Page, rect: fitz.Rect) -> tuple[float, int]:
    """Return (area fraction covered by text, characters of text) inside ``rect``.

    Both matter: a Beamer theorem box is a coloured rounded rectangle full of
    sentences, which looks like a drawing but reads as prose.
    """
    area = rect.width * rect.height
    if area <= 0:
        return 1.0, 0
    covered = 0.0
    chars = 0
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return 0.0, 0
    for block in blocks:
        if len(block) < 5:
            continue
        text = str(block[4]).strip()
        if not text:
            continue
        # Only prose counts against a region. A plot is dense with short labels —
        # axis ticks, units, series names — and rejecting those loses the graphs.
        if len(text) < figure_config.figure_prose_block_min_chars:
            continue
        block_rect = fitz.Rect(block[:4])
        overlap = block_rect & rect
        if overlap.is_empty:
            continue
        covered += overlap.width * overlap.height
        block_area = block_rect.width * block_rect.height
        if block_area > 0:
            # Count only the share of the block that actually falls inside.
            share = (overlap.width * overlap.height) / block_area
            chars += int(len(text) * min(share, 1.0))
    return covered / area, chars


def _with_figure_labels(page: fitz.Page, rect: fitz.Rect) -> fitz.Rect:
    """Grow a region to include the short text blocks that label it.

    Axis ticks, units and node names live in the text layer just *outside* the
    drawing's bounding box. Cropping to the vectors alone hands the vision model an
    unlabelled box, and it invents axes, ranges and units that were never there.
    """
    margin = figure_config.figure_label_margin_pt
    reach = fitz.Rect(rect)
    reach.x0 -= margin
    reach.y0 -= margin
    reach.x1 += margin
    reach.y1 += margin

    grown = fitz.Rect(rect)
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return grown

    for block in blocks:
        if len(block) < 5:
            continue
        text = str(block[4]).strip()
        if not text or len(text) >= figure_config.figure_prose_block_min_chars:
            continue
        block_rect = fitz.Rect(block[:4])
        if block_rect.is_empty or not reach.intersects(block_rect):
            continue
        candidate = grown | block_rect
        if _covers_page(candidate, page.rect):
            continue
        grown = candidate
    return grown


def detect_figure_regions(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    """Find figure regions on a page as (rect, kind), in reading order."""
    vector, raster, kinds = _candidate_rects(page)
    if not vector and not raster:
        return []

    page_rect = page.rect
    gap = figure_config.figure_merge_gap_pt
    vector_blocks = _merge_rects(vector, gap)
    regions: list[tuple[fitz.Rect, str]] = []

    for block in vector_blocks:
        if (
            block.width < figure_config.figure_min_pt
            or block.height < figure_config.figure_min_pt
        ):
            continue
        if _covers_page(block, page_rect):
            continue
        # A cluster of a few strokes is a bullet or a rule, not a diagram.
        members = [r for r in vector if block.intersects(r)]
        if len(members) < figure_config.figure_min_vector_parts:
            continue
        # Slide themes draw their blocks as full-width bands; figures do not.
        wide_limit = figure_config.figure_wide_part_ratio * page_rect.width
        wide = sum(1 for r in members if r.width >= wide_limit)
        if wide / len(members) > figure_config.figure_max_wide_parts:
            continue
        # Theme decoration is filled blocks only; a diagram or plot has strokes.
        fills = sum(1 for r in members if kinds.get(_rect_key(r)) == "f")
        if fills / len(members) > figure_config.figure_max_fill_ratio:
            continue
        text_ratio, text_chars = _text_metrics(page, block)
        if text_ratio > figure_config.figure_max_text_ratio:
            continue
        if text_chars > figure_config.figure_max_text_chars:
            continue
        regions.append((_with_figure_labels(page, block), "vector"))

    for rect in raster:
        merged_into = False
        for i, (existing, kind) in enumerate(regions):
            if existing.intersects(rect):
                combined = fitz.Rect(existing) | rect
                if not _covers_page(combined, page_rect):
                    regions[i] = (combined, "mixed" if kind == "vector" else kind)
                    merged_into = True
                    break
        if not merged_into:
            regions.append((fitz.Rect(rect), "raster"))

    regions.sort(key=lambda item: (round(item[0].y0, 1), round(item[0].x0, 1)))
    return regions[: figure_config.figure_max_per_page]


def render_region(page: fitz.Page, rect: fitz.Rect) -> bytes:
    """Render a page region to PNG at the configured DPI, with a small margin."""
    clip = fitz.Rect(rect)
    pad = figure_config.figure_padding_pt
    clip.x0 -= pad
    clip.y0 -= pad
    clip.x1 += pad
    clip.y1 += pad
    clip &= page.rect

    zoom = figure_config.figure_dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return pix.tobytes("png")


_PAGE_HEADING_RE = re.compile(r"^## Page (\d+)\s*$", flags=re.MULTILINE)


def figure_block(figure: Figure, description: str, *, output_root: Path) -> str:
    """Markdown for one figure: the rendered crop plus its description, if any."""
    try:
        rel = figure.path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        rel = figure.path.name
    alt = f"Figura p{figure.page_number}-{figure.index}"
    lines = [
        f"### Figura {figure.page_number}.{figure.index}",
        "",
        f"![{alt}]({rel})",
    ]
    if description:
        lines += ["", f"> **Figura (descripción generada):** {description}"]
    return "\n".join(lines)


def insert_figure_blocks(markdown: str, blocks_by_page: dict[int, list[str]]) -> str:
    """Place figure blocks at the end of their own ``## Page N`` section."""
    if not blocks_by_page:
        return markdown

    matches = list(_PAGE_HEADING_RE.finditer(markdown))
    if not matches:
        extra = [b for _, blocks in sorted(blocks_by_page.items()) for b in blocks]
        return markdown.rstrip() + "\n\n" + "\n\n".join(extra) + "\n"

    out: list[str] = [markdown[: matches[0].start()]]
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        section = markdown[match.start() : end]
        blocks = blocks_by_page.get(int(match.group(1)))
        if blocks:
            section = section.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n\n"
        out.append(section)
    return "".join(out)


def extract_figures(
    pdf_path: Path,
    assets_dir: Path,
    *,
    doc: fitz.Document | None = None,
) -> list[Figure]:
    """Render every detected figure region in a PDF. Never raises."""
    if not figure_config.figures_enabled:
        return []

    from twomarkdown.converter.pdf_ocr import open_pdf

    figures: list[Figure] = []
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        stem = pdf_path.stem
        with open_pdf(pdf_path, doc) as opened:
            for page_index in range(opened.page_count):
                page = opened[page_index]
                page_number = page_index + 1
                try:
                    regions = detect_figure_regions(page)
                except Exception as exc:
                    logger.debug("Figure detect failed page %s: %s", page_number, exc)
                    continue
                for index, (rect, kind) in enumerate(regions, start=1):
                    try:
                        png = render_region(page, rect)
                    except Exception as exc:
                        logger.debug(
                            "Figure render failed page %s: %s", page_number, exc
                        )
                        continue
                    out_path = assets_dir / f"{stem}-fig-p{page_number}-{index}.png"
                    try:
                        out_path.write_bytes(png)
                    except OSError as exc:
                        logger.warning("Figure write failed %s: %s", out_path, exc)
                        continue
                    figures.append(
                        Figure(
                            page_number=page_number,
                            index=index,
                            path=out_path,
                            rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                            kind=kind,
                        )
                    )
    except Exception as exc:
        logger.warning("Figure extraction failed for %s: %s", pdf_path, exc)
    return figures
