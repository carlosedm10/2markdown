"""Cost a batch before converting it, so the slow work can be ordered and shown.

Measured on this corpus, everything that is not a model call — PDF parsing, table
extraction, text repair, composition — costs about 9 seconds across 58 pages,
while the vision model costs ~1,100. So the only number worth planning around is
"how many pages and figures need the model", and it can be counted cheaply with
the same PyMuPDF pass that conversion would do anyway.

Two uses:

* **Ordering.** Starting the heaviest files first shortens the tail where one
  long document finishes alone (longest-processing-time-first scheduling).
* **Honesty.** An overnight run should say what it is about to do before it
  starts, not after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz

from twomarkdown.config import figure_config, pdf_ocr_config

logger = logging.getLogger(__name__)

# Rough per-unit costs, used only to order work and print an estimate. They are
# deliberately coarse: the point is relative weight, not a promise.
SECONDS_PER_VLM_PAGE = 40.0
SECONDS_PER_FIGURE = 10.0


@dataclass(frozen=True)
class FilePlan:
    """What a single file is expected to cost."""

    path: Path
    pages: int
    vlm_pages: int
    figures: int

    @property
    def weight(self) -> float:
        """Estimated seconds of model time, the only cost that matters."""
        return (
            self.vlm_pages * SECONDS_PER_VLM_PAGE
            + self.figures * SECONDS_PER_FIGURE
        )


@dataclass(frozen=True)
class BatchPlan:
    files: list[FilePlan]

    @property
    def vlm_pages(self) -> int:
        return sum(f.vlm_pages for f in self.files)

    @property
    def figures(self) -> int:
        return sum(f.figures for f in self.files)

    @property
    def pages(self) -> int:
        return sum(f.pages for f in self.files)

    @property
    def estimated_seconds(self) -> float:
        return sum(f.weight for f in self.files)

    def describe(self) -> str:
        """One line the user can act on before committing to a long run."""
        minutes = self.estimated_seconds / 60
        parts = [
            f"{len(self.files)} file(s)",
            f"{self.pages} page(s)",
            f"{self.vlm_pages} needing the vision model",
        ]
        if self.figures:
            parts.append(f"{self.figures} figure(s) to describe")
        estimate = f"~{minutes:.0f} min of model time" if minutes >= 1 else "<1 min"
        return "Plan: " + ", ".join(parts) + f" ({estimate})"


def _plan_pdf(path: Path) -> FilePlan | None:
    """Count the model-bound work in one PDF. None when it cannot be read."""
    from twomarkdown.converter.pdf_ocr import is_scrambled_text

    try:
        doc = fitz.open(path)
    except Exception as exc:
        logger.debug("Plan skipped for %s: %s", path, exc)
        return None

    min_chars = pdf_ocr_config.pdf_ocr_min_chars
    vlm_pages = figures = 0
    try:
        for page in doc:
            try:
                text = page.get_text().strip()
            except Exception:
                continue
            if len(text) < min_chars or is_scrambled_text(text):
                vlm_pages += 1
            if figure_config.figures_enabled and figure_config.describe_figures_llm:
                try:
                    from twomarkdown.converter.figures import detect_figure_regions

                    figures += len(detect_figure_regions(page))
                except Exception:
                    pass
        pages = doc.page_count
    finally:
        doc.close()

    return FilePlan(path=path, pages=pages, vlm_pages=vlm_pages, figures=figures)


def plan_batch(files: list[Path]) -> BatchPlan:
    """Estimate model-bound work per file. Never raises; unknown files cost zero."""
    plans: list[FilePlan] = []
    for path in files:
        plan = None
        if path.suffix.lower() == ".pdf":
            plan = _plan_pdf(path)
        if plan is None:
            plan = FilePlan(path=path, pages=0, vlm_pages=0, figures=0)
        plans.append(plan)
    return BatchPlan(files=plans)


def order_by_cost(files: list[Path], plan: BatchPlan) -> list[Path]:
    """Heaviest first, so no long file is left running alone at the end.

    Ties keep the original discovery order, so a batch with no model work (or an
    unreadable plan) converts in the order the user would expect.
    """
    weights = {f.path: f.weight for f in plan.files}
    return sorted(
        files,
        key=lambda p: (-weights.get(p, 0.0), files.index(p)),
    )
