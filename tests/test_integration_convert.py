"""Integration tests for end-to-end batch conversion."""

from pathlib import Path

import pytest

from src.batch.processor import process_batch


@pytest.mark.integration
class TestIntegrationConvert:
    """Integration tests requiring MarkItDown (and optional Tesseract)."""

    # -------------------------------------------------------------------------
    # HTML conversion
    # -------------------------------------------------------------------------

    def test_process_batch_converts_html_fixture(
        self, batch_dirs: tuple[Path, Path], fixtures_dir: Path
    ) -> None:
        """process_batch() — converts sample.html to sample.md with body text."""
        input_dir, output_dir = batch_dirs
        html_src = fixtures_dir / "sample.html"
        (input_dir / "sample.html").write_text(
            html_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        result = process_batch(
            input_dir,
            output_dir,
            skip_existing=False,
            ocr_enabled=False,
        )

        expected_stats = {"converted": 1, "failed": 0, "skipped": 0}
        assert result.converted == expected_stats["converted"]
        assert result.failed == expected_stats["failed"]
        assert result.skipped == expected_stats["skipped"]

        output_md = output_dir / "sample.md"
        assert output_md.exists()

        body = output_md.read_text(encoding="utf-8")
        assert 'source: "sample.html"' in body or "source: sample.html" in body
        assert "Hello 2markdown" in body or "hello" in body.lower()
