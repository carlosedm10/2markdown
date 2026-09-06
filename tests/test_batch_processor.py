"""Test cases for batch conversion (src.batch.processor)."""

from pathlib import Path
from unittest.mock import patch

from src.batch.processor import process_batch


class TestBatchProcessor:
    """Test cases for process_batch()."""

    # -------------------------------------------------------------------------
    # Soft-fail
    # -------------------------------------------------------------------------

    def test_process_batch_soft_fail_continues_after_one_failure(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — one failed file does not stop conversion of others."""
        input_dir, output_dir = batch_dirs

        good = input_dir / "good.txt"
        good.write_text("Hello world")
        bad = input_dir / "bad.bin"
        bad.write_bytes(b"\x00\x01\x02")

        with patch(
            "src.batch.walker.conversion_config.include_extensions",
            frozenset({".txt", ".bin"}),
        ):
            with patch(
                "src.converter.markitdown_converter.convert_file",
                side_effect=lambda p: "converted" if p.name == "good.txt" else "",
            ):
                result = process_batch(
                    input_dir,
                    output_dir,
                    skip_existing=False,
                    ocr_enabled=False,
                )

        expected_stats = {
            "converted": 1,
            "failed": 1,
            "skipped": 0,
        }
        assert result.converted == expected_stats["converted"]
        assert result.failed == expected_stats["failed"]
        assert result.skipped == expected_stats["skipped"]
        assert (output_dir / "good.md").exists()
        assert not (output_dir / "bad.md").exists()
        assert result.failed_paths == [str(bad.resolve())]

    def test_process_batch_converts_standalone_png_via_ocr_fallback(
        self, batch_dirs: tuple[Path, Path], minimal_png_bytes: bytes
    ) -> None:
        """process_batch() — standalone PNG uses OCR when MarkItDown returns empty."""
        input_dir, output_dir = batch_dirs
        img = input_dir / "diagram.png"
        img.write_bytes(minimal_png_bytes)

        with patch(
            "src.converter.markitdown_converter.convert_file",
            return_value="",
        ):
            with patch(
                "src.converter.ocr.ocr_image_bytes",
                return_value="Diagram text",
            ):
                result = process_batch(
                    input_dir,
                    output_dir,
                    skip_existing=False,
                    ocr_enabled=True,
                )

        assert result.converted == 1
        assert result.failed == 0
        body = (output_dir / "diagram.md").read_text(encoding="utf-8")
        assert "Diagram text" in body

    def test_process_batch_records_failed_status_in_manifest(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — failed file is recorded in .2markdown-manifest.json."""
        input_dir, output_dir = batch_dirs
        bad = input_dir / "bad.bin"
        bad.write_bytes(b"\x00")

        with patch(
            "src.batch.walker.conversion_config.include_extensions",
            frozenset({".bin"}),
        ):
            with patch(
                "src.converter.markitdown_converter.convert_file",
                return_value="",
            ):
                process_batch(
                    input_dir,
                    output_dir,
                    skip_existing=False,
                    ocr_enabled=False,
                )

        manifest_path = output_dir / ".2markdown-manifest.json"
        assert manifest_path.exists()
        content = manifest_path.read_text(encoding="utf-8")
        assert '"status": "failed"' in content
        assert "empty result" in content

    # -------------------------------------------------------------------------
    # Output layout
    # -------------------------------------------------------------------------

    def test_process_batch_mirrors_input_tree_in_output(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — mirrors input/reports/q1/doc.txt to .md under output."""
        input_dir, output_dir = batch_dirs
        nested = input_dir / "reports" / "q1"
        nested.mkdir(parents=True)
        source = nested / "doc.txt"
        source.write_text("Quarterly report")

        with patch(
            "src.converter.markitdown_converter.convert_file",
            return_value="Quarterly report",
        ):
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
            )

        expected_output = output_dir / "reports" / "q1" / "doc.md"
        assert result.converted == 1
        assert expected_output.exists()
        body = expected_output.read_text(encoding="utf-8")
        assert "source: reports/q1/doc.txt" in body
        assert "Quarterly report" in body

    # -------------------------------------------------------------------------
    # Skip existing
    # -------------------------------------------------------------------------

    def test_process_batch_skips_unchanged_when_output_is_newer(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — skip_existing skips file when .md is up to date."""
        input_dir, output_dir = batch_dirs
        source = input_dir / "stale.txt"
        source.write_text("content")
        output_md = output_dir / "stale.md"
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("---\nsource: stale.txt\n---\n\nold")

        with patch(
            "src.converter.markitdown_converter.convert_file",
        ) as mock_convert:
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=True,
                ocr_enabled=False,
            )

        mock_convert.assert_not_called()
        assert result.skipped == 1
        assert result.converted == 0

    def test_process_batch_routes_iwork_bundle_through_iwork_converter(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — .pages bundle uses iwork.convert_bundle, not MarkItDown."""
        input_dir, output_dir = batch_dirs
        pages = input_dir / "notes.pages"
        pages.mkdir()
        (pages / "Metadata").mkdir()
        (pages / "preview.pdf").write_bytes(b"%PDF-1.4\n")

        with patch(
            "src.converter.iwork.is_iwork_bundle",
            return_value=True,
        ):
            with patch(
                "src.converter.iwork.convert_bundle",
                return_value="Page body text",
            ) as mock_iwork:
                with patch(
                    "src.converter.markitdown_converter.convert_file",
                ) as mock_markitdown:
                    result = process_batch(
                        input_dir,
                        output_dir,
                        skip_existing=False,
                        ocr_enabled=False,
                    )

        mock_iwork.assert_called_once()
        mock_markitdown.assert_not_called()
        assert result.converted == 1
        body = (output_dir / "notes.md").read_text(encoding="utf-8")
        assert "Page body text" in body

    def test_process_batch_routes_epub_through_ereader_converter(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — .epub uses ereader.convert_ereader, not MarkItDown."""
        input_dir, output_dir = batch_dirs
        epub = input_dir / "book.epub"
        epub.write_bytes(b"minimal epub")

        with patch(
            "src.converter.ereader.is_ereader",
            return_value=True,
        ):
            with patch(
                "src.converter.ereader.convert_ereader",
                return_value="EPUB body",
            ) as mock_ereader:
                with patch(
                    "src.converter.markitdown_converter.convert_file",
                ) as mock_markitdown:
                    result = process_batch(
                        input_dir,
                        output_dir,
                        skip_existing=False,
                        ocr_enabled=False,
                    )

        mock_ereader.assert_called_once()
        mock_markitdown.assert_not_called()
        assert result.converted == 1
        body = (output_dir / "book.md").read_text(encoding="utf-8")
        assert "EPUB body" in body
