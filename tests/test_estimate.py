"""Time and cost estimation: image-token billing rules, inspect(), estimate()."""

from pathlib import Path

import fitz
import pytest

from twomarkdown.batch import estimate as est
from twomarkdown.batch.estimate import (
    InspectFile,
    InspectResponse,
    InspectTotals,
    Pipeline,
    estimate,
    estimate_image_tokens,
    inspect,
)

LONG_TEXT = "Texto legible de una pagina normal con suficientes caracteres. " * 3


def _make_pdf(
    path: Path, page_texts: list[str], *, size: tuple[float, float] = (612, 792)
) -> Path:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=size[0], height=size[1])
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def _inspect_result(
    path: Path, *, pages: int, scanned_pages: int = 0, figures: int = 0
) -> InspectResponse:
    """Hand-built InspectResponse for one file, so estimate() tests do not have
    to depend on figure/scanned-page detection working a particular way."""
    file = InspectFile(
        path=str(path),
        kind="scanned" if scanned_pages else "text",
        pages=pages,
        scanned_pages=scanned_pages,
        figures=figures,
        text_chars=0,
        bytes=100,
    )
    totals = InspectTotals(
        files=1, pages=pages, scanned_pages=scanned_pages, figures=figures, bytes=100
    )
    return InspectResponse(
        inspect_id="test",
        root=str(path.parent),
        files=[file],
        totals=totals,
        default_output=str(path.parent / f"{path.name}_2markdown"),
    )


def _pipeline(**kw) -> Pipeline:
    return Pipeline(**kw)


# ---------------------------------------------------------------------------
# estimate_image_tokens()
# ---------------------------------------------------------------------------


class TestEstimateImageTokens:
    def test_unknown_provider_returns_none(self) -> None:
        """An anthropic (or any un-implemented) model gets no token estimate."""
        assert estimate_image_tokens(2200, 1650, "anthropic:claude-sonnet-4-5") is None

    def test_bare_tesseract_returns_none(self) -> None:
        """Tesseract is not a vision model billed by tokens."""
        assert estimate_image_tokens(2200, 1650, "tesseract") is None

    def test_non_positive_dimensions_return_none(self) -> None:
        assert estimate_image_tokens(0, 100, "openai:gpt-4o") is None
        assert estimate_image_tokens(100, -1, "openai:gpt-4o") is None

    def test_openai_matches_the_documented_tile_formula(self) -> None:
        """A 2048x2048 image: fits with no shrink, shortest side 2048 -> 768,
        giving a 768x768 image = 4 tiles (2x2) -> 4*170+85 = 765."""
        assert estimate_image_tokens(2048, 2048, "openai:gpt-4o") == 765

    def test_openai_small_image_still_costs_the_base_tokens(self) -> None:
        """A tiny image needs no fitting or shrinking: 1 tile -> 170+85."""
        assert estimate_image_tokens(100, 100, "openai:gpt-4o") == 170 + 85

    def test_openai_never_scales_a_small_image_up(self) -> None:
        """A 200x100 image is already under both the 2048 and 768 caps."""
        # width 200 -> ceil(200/512)=1 tile, height 100 -> 1 tile => 1 tile.
        assert estimate_image_tokens(200, 100, "openai:gpt-4o") == 170 + 85

    def test_openai_scales_with_a_larger_landscape_page(self) -> None:
        """A wide page costs more tiles than a small one, monotonically."""
        small = estimate_image_tokens(300, 300, "openai:gpt-4o")
        large = estimate_image_tokens(4000, 3000, "openai:gpt-4o")
        assert large > small

    def test_qwen_recognises_both_spellings(self) -> None:
        dashed = estimate_image_tokens(1000, 1000, "openai_provider:qwen2.5-vl:7b")
        undashed = estimate_image_tokens(1000, 1000, "ollama:qwen2.5vl:7b")
        assert dashed == undashed
        assert dashed is not None

    def test_qwen_patch_count_scales_with_area(self) -> None:
        small = estimate_image_tokens(560, 560, "ollama:qwen2.5vl:32b")
        large = estimate_image_tokens(2800, 2800, "ollama:qwen2.5vl:32b")
        assert large > small

    def test_qwen_respects_the_minimum_pixel_window(self) -> None:
        """A tiny image is padded up to the documented min_pixels window
        (4*28*28), never billed as a 1x1-patch image."""
        tokens = estimate_image_tokens(10, 10, "ollama:qwen2.5vl:7b")
        assert tokens is not None
        assert tokens >= 4  # min_pixels / 28^2

    def test_qwen_respects_the_maximum_pixel_window(self) -> None:
        """A huge image is clamped down to max_pixels, not billed unbounded."""
        tokens = estimate_image_tokens(20000, 20000, "ollama:qwen2.5vl:7b")
        max_patches = est._QWEN_MAX_PIXELS / (est._QWEN_PATCH_PX**2)
        assert tokens is not None
        assert tokens <= max_patches * 1.05  # rounding to the 28px grid


# ---------------------------------------------------------------------------
# inspect()
# ---------------------------------------------------------------------------


class TestInspect:
    def test_readable_pdf_has_no_scanned_pages(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "clean.pdf", [LONG_TEXT, LONG_TEXT])
        result = inspect(pdf)

        assert result.totals.pages == 2
        assert result.totals.scanned_pages == 0
        assert result.files[0].kind == "text"
        assert result.files[0].text_chars > 0

    def test_blank_pdf_pages_count_as_scanned(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "scan.pdf", ["", "", LONG_TEXT])
        result = inspect(pdf)

        assert result.totals.pages == 3
        assert result.totals.scanned_pages == 2
        assert result.files[0].kind == "scanned"  # majority of pages are scanned

    def test_directory_is_walked_like_a_batch(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        _make_pdf(tmp_path / "b.pdf", ["", ""])
        (tmp_path / "notes.txt").write_text("hola", encoding="utf-8")

        result = inspect(tmp_path)

        assert result.totals.files == 3
        assert result.totals.pages == 3
        assert result.totals.scanned_pages == 2

    def test_non_pdf_kinds_are_classified_by_suffix(self, tmp_path: Path) -> None:
        image = tmp_path / "photo.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        office = tmp_path / "report.docx"
        office.write_bytes(b"PK\x03\x04")
        text = tmp_path / "notes.txt"
        text.write_text("hi", encoding="utf-8")

        result = inspect(tmp_path)
        by_name = {Path(f.path).name: f for f in result.files}

        assert by_name["photo.png"].kind == "image"
        assert by_name["report.docx"].kind == "office"
        assert by_name["notes.txt"].kind == "text"

    def test_corrupt_pdf_does_not_raise(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")

        result = inspect(broken)

        assert result.files[0].kind == "other"
        assert result.totals.pages == 0

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            inspect(tmp_path / "does-not-exist.pdf")

    def test_inspect_id_is_stable_for_the_same_path(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        assert inspect(pdf).inspect_id == inspect(pdf).inspect_id


# ---------------------------------------------------------------------------
# Manual prices
# ---------------------------------------------------------------------------


class TestManualPrices:
    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        est.save_manual_price("openai:brand-new-model", 1.5, 6.0, config_dir=tmp_path)

        prices = est.load_manual_prices(config_dir=tmp_path)

        assert prices["openai:brand-new-model"] == {
            "input_per_mtok": 1.5,
            "output_per_mtok": 6.0,
        }

    def test_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        assert est.load_manual_prices(config_dir=tmp_path / "nope") == {}

    def test_manual_price_wins_over_genai_prices(self, tmp_path: Path) -> None:
        """A manual entry for a real, priced model still overrides the table."""
        est.save_manual_price("openai:gpt-4o", 0.0, 0.0, config_dir=tmp_path)

        usd, unknown = est._cloud_price(
            "openai:gpt-4o", 1_000_000, 1_000_000, config_dir=tmp_path
        )

        assert unknown is False
        assert usd == 0.0

    def test_unknown_model_is_priced_once_a_manual_price_exists(
        self, tmp_path: Path
    ) -> None:
        est.save_manual_price(
            "openai:not-a-real-model-xyz", 2.0, 4.0, config_dir=tmp_path
        )

        usd, unknown = est._cloud_price(
            "openai:not-a-real-model-xyz", 1_000_000, 500_000, config_dir=tmp_path
        )

        assert unknown is False
        assert usd == pytest.approx(2.0 + 2.0)

    def test_unknown_model_without_a_manual_price_is_unknown(
        self, tmp_path: Path
    ) -> None:
        usd, unknown = est._cloud_price(
            "openai:not-a-real-model-xyz", 1000, 100, config_dir=tmp_path
        )

        assert unknown is True
        assert usd is None

    def test_known_model_is_priced_by_genai_prices(self, tmp_path: Path) -> None:
        """No manual override, no network: the bundled genai-prices snapshot
        already prices a real model like gpt-4o-mini."""
        usd, unknown = est._cloud_price(
            "openai:gpt-4o-mini", 1000, 500, config_dir=tmp_path
        )

        assert unknown is False
        assert usd is not None
        assert usd > 0


# ---------------------------------------------------------------------------
# estimate()
# ---------------------------------------------------------------------------


class TestEstimateNoModelWork:
    def test_all_readable_pages_cost_nothing_but_cpu_overhead(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "clean.pdf", [LONG_TEXT] * 5)
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="tesseract", describe_figures=False)

        response = estimate(result, pipeline)

        ocr_stage = next(s for s in response.stages if s.key == "ocr")
        assert ocr_stage.seconds == 0.0
        assert response.total_usd == 0.0
        assert response.bottleneck == "cpu"
        assert response.blocked is None


class TestOcrStage:
    def test_tesseract_is_cpu_and_free(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "scan.pdf", ["", "", ""])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="tesseract")

        response = estimate(result, pipeline)
        ocr_stage = next(s for s in response.stages if s.key == "ocr")

        assert ocr_stage.kind == "cpu"
        assert ocr_stage.seconds == 3 * est.CPU_SECONDS_PER_PAGE
        assert ocr_stage.usd == 0.0

    def test_local_vision_model_is_gpu_and_free(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "scan.pdf", ["", "", ""])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b")

        response = estimate(result, pipeline)
        ocr_stage = next(s for s in response.stages if s.key == "ocr")

        assert ocr_stage.kind == "gpu"
        assert ocr_stage.seconds > 0
        assert ocr_stage.usd == 0.0
        assert ocr_stage.unknown_price is False

    def test_cloud_vision_model_is_priced(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "scan.pdf", ["", "", ""])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="openai:gpt-4o")

        response = estimate(result, pipeline)
        ocr_stage = next(s for s in response.stages if s.key == "ocr")

        assert ocr_stage.kind == "cloud"
        assert ocr_stage.seconds == 3 * est.REMOTE_SECONDS_PER_PAGE_OCR
        assert ocr_stage.unknown_price is False
        assert ocr_stage.usd is not None
        assert ocr_stage.usd > 0

    def test_unknown_cloud_model_has_no_price(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "scan.pdf", [""])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="openai:not-a-real-model-xyz")

        response = estimate(result, pipeline)
        ocr_stage = next(s for s in response.stages if s.key == "ocr")

        assert ocr_stage.unknown_price is True
        assert ocr_stage.usd is None
        assert response.total_usd is None  # never a false $0

    def test_no_scanned_pages_means_zero_ocr_regardless_of_model(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "clean.pdf", [LONG_TEXT])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b")

        ocr_stage = next(s for s in estimate(result, pipeline).stages if s.key == "ocr")

        assert ocr_stage.seconds == 0.0

    def test_per_model_local_rate_is_used_not_the_flat_32b_rate(
        self, tmp_path: Path
    ) -> None:
        """N8: `/api/estimate` used to price every local vision model at the
        flat `LOCAL_SECONDS_PER_VLM_PAGE` (150s/page, measured on
        qwen2.5vl:32b) because `_ocr_stage` imported that constant directly
        instead of going through `planner._local_rates()` — the same table
        the CLI batch planner already consulted. A one-page qwen2.5vl:7b
        estimate must land near the ~40s/page the setup wizard advertises
        for it, not near the 32b rate (previously ~3x over, ~150s)."""
        pdf = _make_pdf(tmp_path / "scan.pdf", [""])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:7b")

        ocr_stage = next(s for s in estimate(result, pipeline).stages if s.key == "ocr")

        assert ocr_stage.kind == "gpu"
        assert ocr_stage.seconds < 60.0  # nowhere near the 32b-rate ~150s
        assert ocr_stage.seconds > 0.0

    def test_local_seconds_scale_with_the_page_image(self, tmp_path: Path) -> None:
        """A tiny page should cost far less local GPU time than a full-size one."""
        small_pdf = _make_pdf(tmp_path / "small.pdf", ["", ""], size=(100, 100))
        large_pdf = _make_pdf(tmp_path / "large.pdf", ["", ""], size=(2000, 2600))
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b")

        small_seconds = next(
            s for s in estimate(inspect(small_pdf), pipeline).stages if s.key == "ocr"
        ).seconds
        large_seconds = next(
            s for s in estimate(inspect(large_pdf), pipeline).stages if s.key == "ocr"
        ).seconds

        assert large_seconds > small_seconds


class TestFiguresStage:
    def test_disabled_pipeline_has_a_none_stage(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        result = _inspect_result(pdf, pages=5, figures=3)
        pipeline = _pipeline(ocr_model="tesseract", describe_figures=False)

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "figures")

        assert stage.kind == "none"
        assert stage.seconds == 0.0

    def test_cloud_figure_model_is_priced_per_figure(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        result = _inspect_result(pdf, pages=1, figures=4)
        pipeline = _pipeline(
            ocr_model="tesseract",
            figure_model="openai:gpt-4o-mini",
            describe_figures=True,
        )

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "figures")

        assert stage.kind == "cloud"
        assert stage.seconds == 4 * est.REMOTE_SECONDS_PER_FIGURE
        assert stage.usd is not None and stage.usd > 0

    def test_no_figures_costs_nothing(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        result = _inspect_result(pdf, pages=1, figures=0)
        pipeline = _pipeline(
            ocr_model="tesseract",
            figure_model="openai:gpt-4o-mini",
            describe_figures=True,
        )

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "figures")
        assert stage.seconds == 0.0


class TestReviewStage:
    def test_disabled_review_has_a_none_stage(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="tesseract", review_model="")

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "review")

        assert stage.kind == "none"
        assert stage.seconds == 0.0

    def test_cloud_review_costs_are_from_assumed_chars(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="tesseract", review_model="openai:gpt-4o-mini")

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "review")

        assert stage.kind == "cloud"
        assert stage.seconds == 2 * est.REMOTE_SECONDS_PER_PAGE_REVIEW
        assert stage.usd is not None and stage.usd > 0

    def test_review_only_runs_on_scanned_pages(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        result = _inspect_result(pdf, pages=1, scanned_pages=0)
        pipeline = _pipeline(ocr_model="tesseract", review_model="openai:gpt-4o-mini")

        stage = next(s for s in estimate(result, pipeline).stages if s.key == "review")
        assert stage.seconds == 0.0


class TestBlockedTwoLocalModels:
    def test_ocr_and_figures_on_the_same_local_model_are_never_blocked(
        self, tmp_path: Path
    ) -> None:
        """Two identical local models never trip the guard."""
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:32b",
            describe_figures=True,
        )

        assert estimate(result, pipeline).blocked is None

    def test_ocr_and_figures_both_local_but_different_are_never_blocked_outright(
        self, tmp_path: Path
    ) -> None:
        """Real engine behaviour: two different local models for OCR and
        figures either fit as-is or fall back to the OCR model
        (`_effective_figure_model`) — either way `blocked` (which only fires
        past a *distinct*-model resident-memory sum) never fires on this
        pair alone, on the same real machine `estimate()` itself would run
        on. See `TestFigureModelCollapseArithmetic` below for the two
        outcomes with the GPU limit pinned to a known value."""
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:7b",
            describe_figures=True,
        )

        assert estimate(result, pipeline).blocked is None


class TestGbFormattingAvoidsRoundingCollisions:
    """N1: a blocked/warning body must never print "39 GB > 39 GB" — the
    line only exists because the two values differ, so rounding them both
    to the same whole number reads as nonsense."""

    def test_format_gb_pair_keeps_integers_when_no_collision(self) -> None:
        assert est._format_gb_pair(29.0, 36.0) == ("29", "36")

    def test_format_gb_pair_falls_back_to_one_decimal_on_collision(self) -> None:
        """39.3 and 38.6 both round to 39 — one decimal (comma-separated,
        Spanish locale) is used for both instead."""
        assert est._format_gb_pair(39.3, 38.6) == ("39,3", "38,6")

    def _pin_mac(self, monkeypatch: pytest.MonkeyPatch, *, ram_gb: float) -> None:
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(system, "ram_gb", lambda: ram_gb)
        monkeypatch.setattr(
            system, "ollama_info", lambda: system.OllamaInfo(running=False, models=[])
        )

    def test_blocked_body_uses_one_decimal_when_rounded_totals_collide(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two distinct local models (29.0 + 10.3 = 39.3 GB) against a 38.6 GB
        limit: `round(39.3) == round(38.6) == 39`, so the body must fall
        back to one decimal rather than printing "39 GB residentes > 39 GB"."""
        self._pin_mac(monkeypatch, ram_gb=51.4667)  # gpu_limit_gb -> 38.6

        def fake_resident_gb(model, installed_sizes=None):
            return {
                "ollama:qwen2.5vl:32b": 29.0,
                "ollama:gemma3:4b": 10.3,
            }.get(model, 0.0)

        monkeypatch.setattr(est, "resident_gb", fake_resident_gb)

        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="ollama:gemma3:4b",
        )

        response = estimate(result, pipeline)

        assert response.gpu_limit_gb == pytest.approx(38.6)
        assert response.blocked is not None
        assert "39,3 GB residentes > 38,6 GB" in response.blocked.body
        # Never the collided-integer form.
        assert "39 GB residentes > 39 GB" not in response.blocked.body


class TestFigureModelCollapseArithmetic:
    """`_effective_figure_model` collapses figures onto the OCR model only
    when the pair would not otherwise fit — not on a blanket "both local"
    rule (see `twomarkdown.batch.gpu_memory.effective_figure_model`, shared
    with `agents.image_ocr.effective_figure_model()` so the engine can never
    disagree with what this estimate told the user to expect)."""

    def _pin_48gb_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(system, "ram_gb", lambda: 48.0)
        monkeypatch.setattr(
            system, "ollama_info", lambda: system.OllamaInfo(running=False, models=[])
        )

    def test_a_pair_that_fits_keeps_its_own_figure_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """qwen2.5vl:7b OCR (~8 GB) + gemma3:4b figures (~5 GB) sit well
        under a 36 GB GPU limit — the figures stage must show gemma3:4b,
        not silently the OCR model."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:7b",
            figure_model="ollama:gemma3:4b",
            describe_figures=True,
        )

        response = estimate(result, pipeline)

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:gemma3:4b"
        assert response.blocked is None
        # C18: nothing was substituted — no note, no effective_model.
        assert figures_stage.note is None
        assert figures_stage.effective_model is None

    def test_a_pair_that_does_not_fit_still_collapses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """qwen2.5vl:32b OCR (~29 GB) + qwen2.5vl:7b figures (~8 GB) do not
        fit in 36 GB — the figures stage falls back to the OCR model, same
        as the pre-arithmetic blanket rule used to always do."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:7b",
            describe_figures=True,
        )

        response = estimate(result, pipeline)

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:qwen2.5vl:32b"
        assert response.blocked is None  # collapsed onto one resident model
        # C18: the app has nothing to render unless the substitution is
        # actually named — `note` is the Spanish, ready-to-show sentence
        # (from `gpu_memory.collapse_message`) and `effective_model` names
        # what will really run (the OCR model, same value as `model` above,
        # since `seconds`/`usd` are already priced for it either way).
        assert figures_stage.note is not None
        assert "qwen2.5vl:7b" in figures_stage.note
        assert "no cabe junto al" in figures_stage.note
        assert figures_stage.effective_model == "ollama:qwen2.5vl:32b"

    def test_the_same_local_model_for_both_is_a_no_op(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:32b",
            describe_figures=True,
        )

        response = estimate(result, pipeline)

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:qwen2.5vl:32b"
        assert figures_stage.note is None
        assert figures_stage.effective_model is None

    def test_a_gemma_figure_model_also_collapses_when_it_does_not_fit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M9: the OCR+figures collapse must apply to *any* local figure
        model, resolved by `is_local_model` (`gpu_memory.
        distinct_local_models`/`effective_figure_model`) — not a name
        heuristic that only ever recognised a "qwen" pair. Pin the GPU limit
        to 30 GB (below 32b OCR's ~29 GB alone plus gemma3:4b's ~4.8 GB) so
        the same pair that fits at the usual 36 GB limit (see
        `test_a_local_pair_that_fits_is_not_collapsed` above, with
        qwen2.5vl:7b OCR instead) genuinely does not fit here."""
        from twomarkdown.server import system

        self._pin_48gb_mac(monkeypatch)
        monkeypatch.setattr(system, "ram_gb", lambda: 40.0)  # gpu_limit_gb -> 30.0
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:gemma3:4b",
            describe_figures=True,
        )

        response = estimate(result, pipeline)

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:qwen2.5vl:32b"
        assert response.blocked is None  # collapsed onto one resident model
        assert response.gpu_resident_gb == pytest.approx(29.0, abs=0.01)

    def test_a_local_gemma_review_model_counts_as_a_second_resident(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M9: unlike a figure model, a local `review_model` never shares
        GPU residency with OCR — even when it names the same small local
        model (gemma3:4b) that a figure model in the same slot would
        collapse onto OCR. 32b OCR (~29 GB) + gemma3:4b review (~4.8 GB)
        must show up as two distinct residents, summing to both, at the
        usual 36 GB limit where the pair still fits."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="ollama:gemma3:4b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is None
        assert response.gpu_resident_gb == pytest.approx(29.0 + 3.3 * 1.45, abs=0.01)


class TestFigureCollapseAccountsForReviewModel:
    """M9: `effective_figure_model` used to decide whether to collapse the
    figure model onto OCR by comparing only the OCR+figure pair, ignoring a
    third distinct local `review_model` that also occupies GPU memory. That
    made the outcome depend on how big the figure model happened to be
    relative to OCR alone, rather than on whether the whole three-model set
    actually fits: a figure model whose pair with OCR alone fit (gemma3:4b)
    stayed uncollapsed even once a local review model pushed the real total
    over the limit, while a figure model whose pair with OCR alone already
    didn't fit (qwen2.5vl:7b) collapsed anyway — by coincidence, not by any
    rule that accounted for review_model. All three of the triples below
    must resolve to the same OCR+review total (43.72 GB) once the figure
    model correctly collapses in every case a local review model is present.
    """

    def _pin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from twomarkdown.batch import gpu_memory

        def fake_resident_gb(model, installed_sizes=None):
            tag = gpu_memory.ollama_tag(model)
            return {
                "qwen2.5vl:32b": 29.0,
                "qwen2.5:14b": 14.72,
                "gemma3:4b": 4.84,
                "qwen2.5vl:7b": 8.12,
            }.get(tag, 0.0)

        monkeypatch.setattr(gpu_memory, "gpu_limit_gb", lambda: 36.0)
        monkeypatch.setattr(gpu_memory, "ollama_installed_sizes", lambda: {})
        monkeypatch.setattr(gpu_memory, "resident_gb", fake_resident_gb)
        monkeypatch.setattr(est, "resident_gb", fake_resident_gb)

    def _pipeline_for(self, figure_model: str | None) -> Pipeline:
        return _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model=figure_model or "",
            describe_figures=bool(figure_model),
            review_model="ollama:qwen2.5:14b",
        )

    def test_gemma_figure_model_now_collapses_with_a_review_model_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Previously stayed uncollapsed (48.56 GB): OCR+gemma alone (33.84)
        fit under the 36 GB limit, so the old pairwise-only check never
        looked at the review model also resident alongside them."""
        self._pin(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)

        response = estimate(result, self._pipeline_for("ollama:gemma3:4b"))

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:qwen2.5vl:32b"  # collapsed onto OCR
        assert response.gpu_resident_gb == pytest.approx(43.72, abs=0.01)

    def test_qwen_figure_model_still_collapses_with_a_review_model_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Already collapsed before the fix (OCR+7b alone, 37.12, already
        exceeds 36 GB on its own) — must keep collapsing, to the same total
        as the gemma3:4b case above."""
        self._pin(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)

        response = estimate(result, self._pipeline_for("ollama:qwen2.5vl:7b"))

        figures_stage = next(s for s in response.stages if s.key == "figures")
        assert figures_stage.model == "ollama:qwen2.5vl:32b"  # collapsed onto OCR
        assert response.gpu_resident_gb == pytest.approx(43.72, abs=0.01)

    def test_no_figure_model_is_the_same_ocr_plus_review_total(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pin(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)

        response = estimate(result, self._pipeline_for(None))

        assert response.gpu_resident_gb == pytest.approx(43.72, abs=0.01)

    def test_gpu_memory_effective_figure_model_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same arithmetic, exercised directly against
        `gpu_memory.effective_figure_model` (what `agents.image_ocr` calls
        during a real conversion) rather than through `estimate()`."""
        from twomarkdown.batch import gpu_memory

        self._pin(monkeypatch)

        resolved, message = gpu_memory.effective_figure_model(
            "ollama:qwen2.5vl:32b", "ollama:gemma3:4b", "ollama:qwen2.5:14b"
        )
        assert resolved == "ollama:qwen2.5vl:32b"
        assert message is not None

    def test_gpu_memory_effective_figure_model_with_no_review_is_unaffected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a review model, the arithmetic is exactly the pairwise
        OCR+figure check it always was — this fix must not change that."""
        from twomarkdown.batch import gpu_memory

        self._pin(monkeypatch)

        resolved, message = gpu_memory.effective_figure_model(
            "ollama:qwen2.5vl:32b", "ollama:gemma3:4b", None
        )
        assert resolved == "ollama:gemma3:4b"  # OCR (29.0) + gemma (4.84) = 33.84 < 36
        assert message is None


class TestBlockedAdviceIsStageAware:
    """N13: the blocked banner's title was already made count-aware (C14),
    but its advice line still hardcoded "Usa el mismo modelo en ambas
    etapas" regardless of how many stages the body actually enumerated."""

    def _pin_48gb_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(system, "ram_gb", lambda: 48.0)
        monkeypatch.setattr(
            system, "ollama_info", lambda: system.OllamaInfo(running=False, models=[])
        )

    def test_two_stages_names_ocr_and_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="ollama:qwen2.5vl:7b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is not None
        assert "Usa el mismo modelo en OCR y revisión" in response.blocked.body
        assert "ambas etapas" not in response.blocked.body

    def test_three_stages_names_all_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Called directly against `_gpu_state` — see the comment on
        `TestGpuResidentMemoryArithmetic.
        test_three_distinct_local_models_gets_a_count_aware_blocked_message`:
        after the M9 fix, a figure model always collapses onto OCR before
        `_gpu_state` could see three distinct models blocked at once through
        the real `estimate()` flow, so this exercises `_gpu_state`'s own
        stage-naming logic directly with three already-distinct models."""
        self._pin_48gb_mac(monkeypatch)

        blocked, _warning, _total, _limit, _headroom = est._gpu_state(
            "ollama:qwen2.5vl:7b", "ollama:qwen2.5vl:3b", "ollama:qwen2.5vl:32b"
        )

        assert blocked is not None
        assert "Usa el mismo modelo en OCR, figuras y revisión" in blocked.body

    def test_a_collapsed_figure_model_is_not_named_as_a_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A figure model that collapsed onto OCR no longer costs its own
        GPU memory, so it must not appear as one of the stages the advice
        names — only the stages that still hold a distinct resident model."""
        from twomarkdown.batch import gpu_memory

        self._pin_48gb_mac(monkeypatch)

        def fake_resident_gb(model, installed_sizes=None):
            tag = gpu_memory.ollama_tag(model)
            return {
                "qwen2.5vl:32b": 29.0,
                "qwen2.5:14b": 14.72,
                "gemma3:4b": 4.84,
            }.get(tag, 0.0)

        monkeypatch.setattr(gpu_memory, "gpu_limit_gb", lambda: 36.0)
        monkeypatch.setattr(gpu_memory, "ollama_installed_sizes", lambda: {})
        monkeypatch.setattr(gpu_memory, "resident_gb", fake_resident_gb)
        monkeypatch.setattr(est, "resident_gb", fake_resident_gb)

        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:gemma3:4b",
            describe_figures=True,
            review_model="ollama:qwen2.5:14b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is not None
        assert "Usa el mismo modelo en OCR y revisión" in response.blocked.body
        assert "figuras" not in response.blocked.body


class TestGbPairEscalatesPrecisionBeforeTying:
    """N14: 38.61 vs 38.60 both round to "38,6" at one decimal — the old
    fallback stopped there and printed the same collided string for both.
    Escalate to two decimals (where they do differ), and only give up with
    an "≈ el límite" phrasing once even two decimals still tie."""

    def test_escalates_to_two_decimals_when_one_decimal_still_collides(self) -> None:
        assert est._format_gb_pair(38.61, 38.60) == ("38,61", "38,60")

    def test_falls_back_to_the_tie_sentinel_past_two_decimals(self) -> None:
        assert est._format_gb_pair(38.601, 38.602) == (
            est.GB_TIE_SENTINEL,
            est.GB_TIE_SENTINEL,
        )

    def test_identical_values_use_the_tie_sentinel(self) -> None:
        assert est._format_gb_pair(40.0, 40.0) == (
            est.GB_TIE_SENTINEL,
            est.GB_TIE_SENTINEL,
        )

    def test_blocked_body_uses_the_tie_sentinel_phrasing(self) -> None:
        """Exercised directly against `_blocked_body`: `total`/`limit` here
        (38.601 vs 38.602) are two genuinely different values that still
        print identically even at the two-decimal cap `_format_gb_pair`
        escalates to — the body must read "≈ el límite", never the
        collided "38,6 GB residentes > 38,6 GB" (or "38,60 > 38,60")."""
        residents = [("qwen2.5vl:32b", 29.0), ("gemma3:4b", 9.601)]

        body = est._blocked_body(residents, 38.601, 38.602, ["OCR", "revisión"])

        assert est.GB_TIE_SENTINEL in body
        assert "38,6 GB residentes > 38,6 GB" not in body
        assert "GB residentes >" not in body


class TestGpuResidentMemoryArithmetic:
    """Replaces the old blanket "two local models are blocked" rule: whether
    a pipeline's distinct local models (after the OCR/figures collapse) are
    blocked is now arithmetic — their combined `RESIDENT_FACTOR`-scaled size
    against `system.gpu_limit_gb` — not a plain headcount. Every test here
    pins the machine to the 48 GB Mac (36 GB GPU limit) these numbers were
    measured on, and Ollama to "not running" so `resident_gb` always falls
    back to the deterministic per-parameter-count table, regardless of
    whatever is actually installed on the machine running the suite.
    """

    def _pin_48gb_mac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from twomarkdown.server import system

        monkeypatch.setattr(system, "is_apple_silicon", lambda: True)
        monkeypatch.setattr(system, "ram_gb", lambda: 48.0)
        monkeypatch.setattr(
            system, "ollama_info", lambda: system.OllamaInfo(running=False, models=[])
        )

    def test_32b_and_7b_is_blocked_with_the_arithmetic_in_the_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """20 GB file * 1.45 = 29 GB, 5.6 GB file * 1.45 ≈ 8 GB; 37 > 36."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="ollama:qwen2.5vl:7b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is not None
        assert "GPU" in response.blocked.title
        body = response.blocked.body
        assert "qwen2.5vl:32b" in body and "29 GB" in body
        assert "qwen2.5vl:7b" in body and "8 GB" in body
        assert "37 GB" in body
        assert "36 GB" in body
        assert response.warning is None
        assert response.gpu_resident_gb == pytest.approx(37.12, abs=0.01)
        assert response.gpu_limit_gb == pytest.approx(36.0)
        assert response.gpu_headroom_gb < 0

    def test_three_distinct_local_models_gets_a_count_aware_blocked_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C14: OCR/figures/review each naming a different local model can
        enumerate three distinct residents, not just two — the blocked
        banner's title (and the "con ambos/todos cargados" clause in its
        body) must match that count instead of hardcoding "Dos modelos"
        while the body lists three.

        Called directly against `_gpu_state` (rather than through
        `estimate()`) because M9's fix makes the *figure* model the one
        role that collapses onto OCR whenever a genuine three-way total
        would not fit — so end-to-end, a figure model always collapses
        before three distinct models could ever reach `_gpu_state`
        simultaneously blocked. The count-aware title/body wording this
        test checks is `_gpu_state`'s own responsibility regardless of how
        many distinct models a caller hands it, so it is exercised here on
        its own terms.
        """
        self._pin_48gb_mac(monkeypatch)

        blocked, _warning, _total, _limit, _headroom = est._gpu_state(
            "ollama:qwen2.5vl:7b", "ollama:qwen2.5vl:3b", "ollama:qwen2.5vl:32b"
        )

        assert blocked is not None
        assert "Dos modelos" not in blocked.title
        assert "GPU" in blocked.title
        body = blocked.body
        assert "qwen2.5vl:7b" in body
        assert "qwen2.5vl:3b" in body
        assert "qwen2.5vl:32b" in body
        assert "Con ambos cargados" not in body
        assert "todos cargados" in body

    def test_32b_and_3b_fits_with_a_tight_headroom_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """29 + ~4.6 ≈ 34 GB of 36 — under the limit, but under 3 GB spare."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="ollama:qwen2.5vl:3b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is None
        assert response.warning is not None
        assert "34" in response.warning.body
        assert "36" in response.warning.body
        assert response.gpu_headroom_gb < 3.0
        assert response.gpu_headroom_gb >= 0

    def test_7b_and_3b_fits_with_no_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:7b",
            review_model="ollama:qwen2.5vl:3b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is None
        assert response.warning is None
        assert response.gpu_headroom_gb >= 3.0

    def test_same_model_twice_is_never_blocked_regardless_of_headroom(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single distinct local model can never trip the guard, even one
        that alone leaves under 3 GB of headroom (72b: 45 * 1.45 ≈ 65 GB, well
        past the 36 GB limit) — `blocked` requires *two* distinct models."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:72b",
            review_model="ollama:qwen2.5vl:72b",
        )

        response = estimate(result, pipeline)

        assert response.blocked is None

    def test_figures_collapsed_onto_ocr_are_not_counted_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OCR 32b + figures 7b (collapsed onto 32b by effective_figure_model)
        must resident-cost as one 32b, not 32b + 7b — so a *third*, distinct
        local review model is what should decide blocked/not, not the figure
        model that never really loads."""
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            figure_model="ollama:qwen2.5vl:7b",
            describe_figures=True,
        )

        response = estimate(result, pipeline)

        assert response.blocked is None
        assert response.gpu_resident_gb == pytest.approx(29.0, abs=0.01)

    def test_hosted_model_never_counts_towards_resident_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._pin_48gb_mac(monkeypatch)
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1)
        pipeline = _pipeline(
            ocr_model="ollama:qwen2.5vl:32b",
            review_model="openai:gpt-4o-mini",
        )

        response = estimate(result, pipeline)

        assert response.blocked is None
        assert response.gpu_resident_gb == pytest.approx(29.0, abs=0.01)


class TestResidentGb:
    def test_uses_the_installed_size_when_ollama_reports_one(self) -> None:
        assert est.resident_gb(
            "ollama:qwen2.5vl:32b", installed_sizes={"qwen2.5vl:32b": 20.0}
        ) == pytest.approx(29.0)

    def test_falls_back_to_the_per_parameter_table_when_not_installed(self) -> None:
        assert est.resident_gb(
            "ollama:qwen2.5vl:7b", installed_sizes={}
        ) == pytest.approx(5.6 * 1.45)

    def test_a_hosted_model_is_always_zero(self) -> None:
        assert est.resident_gb("openai:gpt-4o") == 0.0

    def test_an_unknown_local_model_with_no_size_is_zero_not_a_guess(self) -> None:
        assert est.resident_gb("ollama:mystery-model", installed_sizes={}) == 0.0

    def test_27b_is_not_misread_as_7b(self) -> None:
        """`_PARAM_COUNT_RE` must not match "7b" inside "27b"."""
        from twomarkdown.batch import gpu_memory

        assert gpu_memory.fallback_file_gb("gemma3:27b") is None

    def test_gemma3_4b_has_a_fallback_size(self) -> None:
        from twomarkdown.batch import gpu_memory

        assert gpu_memory.fallback_file_gb("gemma3:4b") == pytest.approx(3.3)


class TestScheduling:
    def test_a_local_stage_forces_parallel_files_to_one(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b", workers=8)

        response = estimate(result, pipeline)

        assert response.parallel_files == 1
        assert response.bottleneck == "gpu"

    def test_cloud_only_keeps_the_requested_workers_up_to_four(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", "", "", ""])
        result = _inspect_result(pdf, pages=4, scanned_pages=4)
        pipeline = _pipeline(ocr_model="openai:gpt-4o", workers=8)

        response = estimate(result, pipeline)

        assert response.parallel_files == 8
        assert response.bottleneck == "cloud"
        ocr_stage = next(s for s in response.stages if s.key == "ocr")
        # 4 permits max, however many workers are requested.
        assert response.total_seconds == pytest.approx(ocr_stage.seconds / 4)

    def test_cpu_only_batch_scales_down_with_more_workers(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [""] * 8)
        result = _inspect_result(pdf, pages=8, scanned_pages=8)
        pipeline_one = _pipeline(ocr_model="tesseract", workers=1)
        pipeline_four = _pipeline(ocr_model="tesseract", workers=4)

        slow = estimate(result, pipeline_one).total_seconds
        fast = estimate(result, pipeline_four).total_seconds

        assert fast < slow
        assert estimate(result, pipeline_four).bottleneck == "cpu"


class TestAlternatives:
    def test_gpu_bottleneck_offers_a_cloud_and_a_tesseract_alternative(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b")

        response = estimate(result, pipeline)
        labels = {a.label for a in response.alternatives}

        assert "Leer también en la nube (gpt-4o)" in labels
        assert "Tesseract en vez del modelo de OCR" in labels

    def test_already_tesseract_does_not_offer_itself_as_an_alternative(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="tesseract")

        response = estimate(result, pipeline)

        assert all(
            a.pipeline_patch.get("ocr_model") != "tesseract"
            for a in response.alternatives
        )

    def test_figures_alternative_only_appears_when_figures_are_enabled(
        self, tmp_path: Path
    ) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [""])
        result = _inspect_result(pdf, pages=1, scanned_pages=1, figures=2)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b", describe_figures=False)

        response = estimate(result, pipeline)

        assert all(
            a.pipeline_patch.get("figure_model") is None for a in response.alternatives
        )

    def test_alternatives_do_not_recurse_infinitely(self, tmp_path: Path) -> None:
        """Each alternative is priced with `_for_alternative=True` and must not
        carry its own nested alternatives."""
        pdf = _make_pdf(tmp_path / "a.pdf", ["", ""])
        result = _inspect_result(pdf, pages=2, scanned_pages=2)
        pipeline = _pipeline(ocr_model="ollama:qwen2.5vl:32b")

        response = estimate(result, pipeline)

        assert len(response.alternatives) > 0
        # The public estimate() call itself is not recursing forever, which
        # this test would hang or blow the stack on if it were.


class TestEstimateStagesAreComplete:
    def test_every_stage_key_is_present(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "a.pdf", [LONG_TEXT])
        result = inspect(pdf)
        pipeline = _pipeline(ocr_model="tesseract")

        keys = {s.key for s in estimate(result, pipeline).stages}

        assert keys == {"extract", "ocr", "figures", "review", "write"}
