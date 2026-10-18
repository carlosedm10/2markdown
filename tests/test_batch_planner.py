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


class TestTimeoutBudget:
    """A long file must not be killed for being long."""

    def test_budget_grows_with_the_work(self) -> None:
        """FilePlan.weight — 28 model pages must earn more time than 2."""
        small = FilePlan(Path("a.pdf"), pages=3, vlm_pages=2, figures=0)
        large = FilePlan(Path("b.pdf"), pages=90, vlm_pages=28, figures=30)

        assert large.weight > small.weight * 10

    def test_local_and_remote_budgets_differ(self, monkeypatch) -> None:
        """FilePlan.weight — a hosted model is far quicker, and the plan knows."""
        from twomarkdown.config import llm_config

        plan = FilePlan(Path("a.pdf"), pages=30, vlm_pages=25, figures=0)

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        local = plan.weight
        monkeypatch.setattr(llm_config, "vision_model", "openai:gpt-4o")
        remote = plan.weight

        assert local > remote * 3

    def test_file_with_no_model_work_costs_nothing(self) -> None:
        """FilePlan.weight — a fully digital PDF needs no allowance at all."""
        assert FilePlan(Path("a.pdf"), pages=50, vlm_pages=0, figures=0).weight == 0


class TestFileTimeoutBudget:
    """The clock that cuts a file off must use that file's own budget."""

    def test_heavy_file_gets_more_than_the_floor(self) -> None:
        """file_timeout_budget() — 60 minutes of work is not cut off at 10."""
        from twomarkdown.batch.processor import file_timeout_budget

        assert file_timeout_budget(3600.0, floor=600.0, factor=1.0) == 3600.0

    def test_small_file_still_gets_the_floor(self) -> None:
        """file_timeout_budget() — a quick file keeps a usable minimum."""
        from twomarkdown.batch.processor import file_timeout_budget

        assert file_timeout_budget(5.0, floor=600.0, factor=1.0) == 600.0

    def test_factor_scales_the_estimate(self) -> None:
        """file_timeout_budget() — headroom multiplies the estimate."""
        from twomarkdown.batch.processor import file_timeout_budget

        assert file_timeout_budget(1000.0, floor=600.0, factor=3.0) == 3000.0

    def test_disabled_timeout_stays_disabled(self) -> None:
        """file_timeout_budget() — no floor means no deadline at all."""
        from twomarkdown.batch.processor import file_timeout_budget

        assert file_timeout_budget(9999.0, floor=None, factor=3.0) is None


class TestEffectiveWorkers:
    """Extra workers cannot help when one GPU is the whole batch."""

    def test_a_local_vision_model_runs_one_file_at_a_time(self, monkeypatch) -> None:
        """effective_workers() — queueing on the GPU still burns the file's clock.

        Four workers on 8 handwritten PDFs all hit the 600s per-file timeout
        without finishing a page: three of the four were always waiting for the
        single vision permit, and that wait counts against them.
        """
        from twomarkdown.batch.processor import effective_workers
        from twomarkdown.config import conversion_config, llm_config

        monkeypatch.setattr(conversion_config, "parallel_workers", 4)
        monkeypatch.setattr(conversion_config, "ocr_enabled", True)
        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")

        assert effective_workers() == 1

    def test_a_hosted_model_keeps_every_worker(self, monkeypatch) -> None:
        """effective_workers() — a hosted provider answers many files at once."""
        from twomarkdown.batch.processor import effective_workers
        from twomarkdown.config import conversion_config, llm_config

        monkeypatch.setattr(conversion_config, "parallel_workers", 4)
        monkeypatch.setattr(conversion_config, "ocr_enabled", True)
        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "vision_model", "openai:gpt-4o")

        assert effective_workers() == 4

    def test_without_a_vision_model_nothing_is_serialized(self, monkeypatch) -> None:
        """effective_workers() — Tesseract and plain parsing are CPU work."""
        from twomarkdown.batch.processor import effective_workers
        from twomarkdown.config import conversion_config, llm_config

        monkeypatch.setattr(conversion_config, "parallel_workers", 4)
        monkeypatch.setattr(llm_config, "llm_enabled", False)

        assert effective_workers() == 4


class TestLocalPageBudgetMatchesReality:
    """The estimate has to be the measured cost, or every budget is short."""

    def test_a_local_page_is_costed_at_what_it_measured(self, monkeypatch) -> None:
        """LOCAL_SECONDS_PER_VLM_PAGE — a handwritten page took 87-160s.

        The previous 60s came from printed slides. Applied to scans it sized a
        six-page file at 18 minutes of allowance for work that needs 15, and the
        whole batch was killed mid-page.
        """
        from twomarkdown.batch import planner
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        plan = planner.FilePlan(Path("a.pdf"), pages=6, vlm_pages=6, figures=0)

        assert plan.weight >= 6 * 87.0

    def test_a_multi_page_scan_is_given_far_more_than_the_floor(
        self, monkeypatch
    ) -> None:
        """file_timeout_budget() — six scanned pages must outlast a 10-minute floor."""
        from twomarkdown.batch import planner
        from twomarkdown.batch.processor import file_timeout_budget
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        plan = planner.FilePlan(Path("a.pdf"), pages=6, vlm_pages=6, figures=0)

        assert file_timeout_budget(plan.weight, floor=600.0, factor=3.0) > 2400.0

    def test_the_smaller_local_model_is_not_costed_at_the_bigger_ones_rate(
        self, monkeypatch
    ) -> None:
        """N8: `qwen2.5vl:7b` (the wizard's own "~40 s/página") must not be
        priced at the ~150s/page measured for `qwen2.5vl:32b` — a single-page
        7b estimate of "~3 min" against a real ~62s run was a ~3x overshoot,
        and disagreed with the wizard's own per-model copy for the same
        model on the same machine."""
        from twomarkdown.batch import planner
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:7b")
        one_page = planner.FilePlan(Path("a.pdf"), pages=1, vlm_pages=1, figures=0)

        assert one_page.weight <= 60.0  # nowhere near the ~150s 32b rate

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        still_32b = planner.FilePlan(Path("b.pdf"), pages=1, vlm_pages=1, figures=0)

        assert still_32b.weight >= 87.0  # the 32b measurement is unaffected
