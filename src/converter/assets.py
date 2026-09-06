"""Extract embedded raster images from PDFs and build markdown asset indexes."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from src.config import conversion_config

logger = logging.getLogger(__name__)


def extract_pdf_images(pdf_path: Path, assets_dir: Path) -> list[tuple[Path, int]]:
    """Extract embedded images from a PDF into ``assets_dir``.

    Returns ``(saved_path, page_number)`` tuples. Never raises; logs warnings.
    """
    if not conversion_config.extract_assets:
        return []

    saved: list[tuple[Path, int]] = []
    min_px = conversion_config.min_image_px

    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as doc:
            stem = pdf_path.stem
            for page_index in range(len(doc)):
                page_number = page_index + 1
                page = doc[page_index]
                try:
                    image_infos = page.get_images(full=True)
                except Exception as exc:
                    logger.warning(
                        "Failed to list images on page %s of %s: %s",
                        page_number,
                        pdf_path,
                        exc,
                    )
                    continue

                image_num = 0
                for img_info in image_infos:
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                    except Exception as exc:
                        logger.warning(
                            "Failed to extract image xref %s from %s page %s: %s",
                            xref,
                            pdf_path,
                            page_number,
                            exc,
                        )
                        continue

                    width = int(base_image.get("width") or 0)
                    height = int(base_image.get("height") or 0)
                    if width < min_px or height < min_px:
                        continue

                    image_num += 1
                    ext = (base_image.get("ext") or "png").lstrip(".")
                    out_name = f"{stem}-p{page_number}-{image_num}.{ext}"
                    out_path = assets_dir / out_name
                    try:
                        out_path.write_bytes(base_image["image"])
                    except Exception as exc:
                        logger.warning(
                            "Failed to write image %s from %s: %s",
                            out_name,
                            pdf_path,
                            exc,
                        )
                        continue
                    saved.append((out_path, page_number))
    except Exception as exc:
        logger.warning("PDF image extraction failed for %s: %s", pdf_path, exc)

    return saved


def markdown_asset_index(
    assets: list[tuple[Path, int]],
    *,
    output_root: Path,
) -> str:
    """Build a markdown section linking extracted assets relative to ``output_root``."""
    if not assets:
        return ""

    root = output_root.resolve()
    lines = ["## Embedded images", ""]
    for asset_path, page_number in sorted(
        assets, key=lambda item: (item[1], str(item[0]))
    ):
        try:
            rel = asset_path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = asset_path.name
        alt = asset_path.stem
        lines.append(f"- Page {page_number}: ![{alt}]({rel})")
    lines.append("")
    return "\n".join(lines)
