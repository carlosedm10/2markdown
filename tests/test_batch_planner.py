"""Costing a batch before converting it, and running the heaviest files first."""

from pathlib import Path

import fitz

from twomarkdown.batch.planner import (
    BatchPlan,
    FilePlan,
    order_by_cost,
    plan_batch,
)

LONG_TEXT = "Texto legible de una pagina normal con suficientes caracteres. " * 3


def _make_pdf(path: Path, page_texts: list[str]) -> Path:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


class TestPlanBatch:
    def test_counts_pages_needing_the_model(self, tmp_path: Path) -> None:
        """plan_batch() — blank pages are model work, readable ones are not."""
        pdf = _make_pdf(tmp_path / "mixed.pdf", [LONG_TEXT, "", "", LONG_TEXT])
        plan = plan_batch([pdf])

        assert plan.pages == 4
        assert plan.vlm_pages == 2

    def test_readable_pdf_costs_nothing(self, tmp_path: Path) -> None:
        """plan_batch() — a fully digital PDF needs no model time."""
        pdf = _make_pdf(tmp_path / "clean.pdf", [LONG_TEXT, LONG_TEXT])
        plan = plan_batch([pdf])

        assert plan.vlm_pages == 0
        assert plan.estimated_seconds == 0

    def test_unreadable_file_does_not_raise(self, tmp_path: Path) -> None:
        """plan_batch() — a corrupt PDF is costed at zero, not an exception."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")

        plan = plan_batch([broken])

        assert len(plan.files) == 1
        assert plan.vlm_pages == 0

    def test_non_pdf_is_costed_at_zero(self, tmp_path: Path) -> None:
        """plan_batch() — only PDFs carry model-bound work worth ordering by."""
        other = tmp_path / "notes.txt"
        other.write_text("hola", encoding="utf-8")

        assert plan_batch([other]).estimated_seconds == 0


class TestDescribe:
    def test_describe_reports_the_work(self) -> None:
        """describe() — states files, pages and model work before a long run."""
        plan = BatchPlan(
            files=[FilePlan(Path("a.pdf"), pages=90, vlm_pages=60, figures=0)]
        )
        text = plan.describe()

        assert "1 file(s)" in text
        assert "90 page(s)" in text
        assert "60 needing the vision model" in text
        assert "min of model time" in text

    def test_describe_handles_an_empty_batch(self) -> None:
        """describe() — nothing to do reads as nothing to do."""
        assert "0 file(s)" in BatchPlan(files=[]).describe()


class TestOrderByCost:
    def test_heaviest_file_runs_first(self, tmp_path: Path) -> None:
        """order_by_cost() — longest-processing-time-first shortens the tail."""
        light = tmp_path / "light.pdf"
        heavy = tmp_path / "heavy.pdf"
        plan = BatchPlan(
            files=[
                FilePlan(light, pages=2, vlm_pages=1, figures=0),
                FilePlan(heavy, pages=90, vlm_pages=60, figures=0),
            ]
        )

        assert order_by_cost([light, heavy], plan) == [heavy, light]

    def test_equal_cost_keeps_discovery_order(self, tmp_path: Path) -> None:
        """order_by_cost() — with no model work, the original order is kept."""
        a, b, c = (tmp_path / n for n in ("a.pdf", "b.pdf", "c.pdf"))
        plan = BatchPlan(
            files=[FilePlan(p, pages=1, vlm_pages=0, figures=0) for p in (a, b, c)]
        )

        assert order_by_cost([a, b, c], plan) == [a, b, c]

    def test_unplanned_file_is_not_dropped(self, tmp_path: Path) -> None:
        """order_by_cost() — a file missing from the plan still gets converted."""
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        plan = BatchPlan(files=[FilePlan(a, pages=5, vlm_pages=5, figures=0)])

        ordered = order_by_cost([a, b], plan)

        assert set(ordered) == {a, b}
        assert ordered[0] == a
