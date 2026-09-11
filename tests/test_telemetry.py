"""Export report and span collector."""

from pathlib import Path

from twomarkdown.batch.manifest import FileRecord, Manifest
from twomarkdown.batch.processor import process_batch
from twomarkdown.telemetry.collector import (
    FileTrace,
    begin_batch,
    begin_file,
    end_batch,
    end_file,
    span,
)
from twomarkdown.telemetry.report import render_html, write_html, write_pdf
from twomarkdown.telemetry.summary import build_summary, reliability_for_file


class TestReliability:
    def test_failed_is_zero(self) -> None:
        record = FileRecord(source="a.txt", status="failed")
        assert reliability_for_file(record, None) == 0.0

    def test_skipped_is_none(self) -> None:
        record = FileRecord(source="a.txt", status="skipped")
        assert reliability_for_file(record, None) is None

    def test_ok_without_ocr_is_one(self) -> None:
        record = FileRecord(source="a.txt", status="ok", char_count=12)
        assert reliability_for_file(record, None) == 1.0

    def test_ok_empty_is_low(self) -> None:
        record = FileRecord(source="a.txt", status="ok", char_count=0)
        assert reliability_for_file(record, None) == 0.15

    def test_ocr_confidence_scales(self) -> None:
        record = FileRecord(source="a.png", status="ok", char_count=80)
        trace = FileTrace(
            source="a.png",
            notes=[{"kind": "ocr.tesseract", "confidence": 50}],
        )
        score = reliability_for_file(record, trace)
        assert score == 0.7


class TestCollector:
    def test_nested_spans_record_duration(self) -> None:
        begin_batch()
        begin_file(Path("/tmp/x.txt"))
        with span("outer"):
            with span("inner"):
                pass
        trace = end_file()
        end_batch()
        assert trace.spans[0].name == "outer"
        assert trace.spans[0].children[0].name == "inner"
        assert "outer" in trace.span_totals()
        assert "inner" in trace.span_totals()


class TestReport:
    def test_process_batch_writes_html_and_pdf(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        input_dir, output_dir = batch_dirs
        (input_dir / "notes.txt").write_text("hello")
        from unittest.mock import patch

        with patch(
            "twomarkdown.converter.markitdown_converter.convert_file",
            return_value="hello",
        ):
            process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
                show_progress=False,
            )

        html_path = output_dir / "2markdown-report.html"
        pdf_path = output_dir / "2markdown-report.pdf"
        trace_path = output_dir / ".2markdown-trace.json"
        assert html_path.is_file()
        assert pdf_path.is_file()
        assert pdf_path.stat().st_size > 200
        assert trace_path.is_file()
        body = html_path.read_text(encoding="utf-8")
        assert "Exported" in body
        assert "notes.txt" in body
        assert "markitdown" in body
        trace = trace_path.read_text(encoding="utf-8")
        assert "notes.txt" in trace
        assert '"converter": "markitdown"' in trace

    def test_render_html_lists_failures(self, tmp_path: Path) -> None:
        manifest = Manifest(tmp_path / ".2markdown-manifest.json")
        manifest.record(
            tmp_path / "bad.bin",
            status="failed",
            error="empty result",
            duration_ms=12,
        )
        summary = build_summary(
            manifest=manifest,
            traces=[],
            batch_spans=[],
            wall_ms=20,
            input_dir=tmp_path,
            output_dir=tmp_path,
            converted=0,
            failed=1,
            skipped=0,
            config={"ocr_enabled": False},
        )
        html = render_html(summary)
        assert "empty result" in html
        write_html(summary, tmp_path)
        write_pdf(summary, tmp_path)
        assert (tmp_path / "2markdown-report.pdf").stat().st_size > 200

    def test_render_html_caps_long_failure_lists(self, tmp_path: Path) -> None:
        manifest = Manifest(tmp_path / ".2markdown-manifest.json")
        for index in range(60):
            manifest.record(
                tmp_path / f"bad{index}.bin",
                status="failed",
                error="timeout after 300.0s",
                duration_ms=300_000,
            )
        summary = build_summary(
            manifest=manifest,
            traces=[],
            batch_spans=[],
            wall_ms=20,
            input_dir=tmp_path,
            output_dir=tmp_path,
            converted=0,
            failed=60,
            skipped=0,
            config={"ocr_enabled": False},
        )
        html = render_html(summary)
        assert "Failures (60)" in html
        assert "10 more failures" in html
        fail_list = html.split("Failures (60)", 1)[1].split("</ul>", 1)[0]
        assert fail_list.count("<li>") == 51
