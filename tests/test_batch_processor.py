"""Test cases for batch conversion (twomarkdown.batch.processor)."""

import json
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from twomarkdown.batch.processor import _compose_pdf, process_batch
from twomarkdown.converter.markitdown_converter import ConversionError


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
            "twomarkdown.batch.walker.conversion_config.include_extensions",
            frozenset({".txt", ".bin"}),
        ):
            with patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
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
            "twomarkdown.converter.markitdown_converter.convert_file",
            return_value="",
        ):
            with patch(
                "twomarkdown.converter.ocr.ocr_image_bytes",
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

    def test_process_batch_ocrs_png_bytes_named_as_pdf(
        self, batch_dirs: tuple[Path, Path], minimal_png_bytes: bytes
    ) -> None:
        """process_batch() — PNG magic wins over a .pdf extension."""
        input_dir, output_dir = batch_dirs
        source = input_dir / "Foto.pdf"
        source.write_bytes(minimal_png_bytes)

        with patch(
            "twomarkdown.converter.markitdown_converter.convert_file",
            return_value="",
        ):
            with patch(
                "twomarkdown.converter.ocr.ocr_image_bytes",
                return_value="Handwritten tree",
            ):
                result = process_batch(
                    input_dir,
                    output_dir,
                    skip_existing=False,
                    ocr_enabled=True,
                )

        assert result.converted == 1
        assert result.failed == 0
        body = (output_dir / "Foto.md").read_text(encoding="utf-8")
        assert "Handwritten tree" in body

    def test_process_batch_writes_placeholder_when_image_has_no_text(
        self, batch_dirs: tuple[Path, Path], minimal_png_bytes: bytes
    ) -> None:
        """process_batch() — logos with no OCR text still write markdown."""
        input_dir, output_dir = batch_dirs
        source = input_dir / "Logo.png"
        source.write_bytes(minimal_png_bytes)

        with patch(
            "twomarkdown.converter.markitdown_converter.convert_file",
            return_value="",
        ):
            with patch(
                "twomarkdown.converter.ocr.ocr_image_bytes",
                return_value="",
            ):
                result = process_batch(
                    input_dir,
                    output_dir,
                    skip_existing=False,
                    ocr_enabled=True,
                )

        assert result.converted == 1
        assert result.failed == 0
        body = (output_dir / "Logo.md").read_text(encoding="utf-8")
        assert "No text extracted" in body

    def test_process_batch_records_failed_status_in_manifest(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — failed file is recorded in .2markdown-manifest.json."""
        input_dir, output_dir = batch_dirs
        bad = input_dir / "bad.bin"
        bad.write_bytes(b"\x00")

        with patch(
            "twomarkdown.batch.walker.conversion_config.include_extensions",
            frozenset({".bin"}),
        ):
            with patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
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
            "twomarkdown.converter.markitdown_converter.convert_file",
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
        assert 'source: "reports/q1/doc.txt"' in body
        assert "Quarterly report" in body

    def test_process_batch_explodes_zip_as_output_folder(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — zip members convert as if the zip were a folder."""
        input_dir, output_dir = batch_dirs
        nested = input_dir / "folder"
        nested.mkdir()
        zip_path = nested / "Recurso de Alzada.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("Alzada.txt", b"appeal body")
            archive.writestr(".hidden.txt", b"skip me")
            archive.writestr("ignore.bin", b"\x00")

        with patch(
            "twomarkdown.converter.markitdown_converter.convert_file",
            return_value="appeal body",
        ):
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
            )

        expected_output = output_dir / "folder" / "Recurso de Alzada" / "Alzada.md"
        assert result.converted == 1
        assert result.failed == 0
        assert expected_output.exists()
        body = expected_output.read_text(encoding="utf-8")
        assert 'source: "folder/Recurso de Alzada/Alzada.txt"' in body
        assert "appeal body" in body
        assert not (output_dir / "folder" / "Recurso de Alzada" / "ignore.md").exists()

    def test_process_batch_converts_utf8_json_without_ascii_codec(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — JSON with non-ASCII bytes converts as UTF-8."""
        input_dir, output_dir = batch_dirs
        source = input_dir / "content.json"
        source.write_bytes(b'{"titulo": "Alzada \xc2\xba"}')

        result = process_batch(
            input_dir,
            output_dir,
            skip_existing=False,
            ocr_enabled=False,
        )

        expected = output_dir / "content.md"
        assert result.converted == 1
        assert result.failed == 0
        assert expected.exists()
        body = expected.read_text(encoding="utf-8")
        assert "Alzada º" in body
        assert "```json" in body

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
            "twomarkdown.converter.markitdown_converter.convert_file",
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
            "twomarkdown.converter.iwork.is_iwork_bundle",
            return_value=True,
        ):
            with patch(
                "twomarkdown.converter.iwork.convert_bundle",
                return_value="Page body text",
            ) as mock_iwork:
                with patch(
                    "twomarkdown.converter.markitdown_converter.convert_file",
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
            "twomarkdown.converter.ereader.is_ereader",
            return_value=True,
        ):
            with patch(
                "twomarkdown.converter.ereader.convert_ereader",
                return_value="EPUB body",
            ) as mock_ereader:
                with patch(
                    "twomarkdown.converter.markitdown_converter.convert_file",
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

    def test_process_batch_dry_run_does_not_write_markdown(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch(dry_run=True) — lists files and writes nothing."""
        input_dir, output_dir = batch_dirs
        source = input_dir / "notes.txt"
        source.write_text("hello")

        result = process_batch(
            input_dir,
            output_dir,
            skip_existing=False,
            ocr_enabled=False,
            dry_run=True,
        )

        assert result.converted == 0
        assert str(source.resolve()) in result.planned
        assert not (output_dir / "notes.md").exists()

    def test_process_batch_times_out_sequential_file(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — sequential timeout is recorded without hanging."""
        input_dir, output_dir = batch_dirs
        hung = input_dir / "slow.txt"
        hung.write_text("slow")
        fast = input_dir / "fast.txt"
        fast.write_text("fast")

        def convert(path: Path) -> str:
            if path.name == "slow.txt":
                time.sleep(5)
            return "ok"

        with (
            patch(
                "twomarkdown.batch.processor.conversion_config.file_timeout_sec",
                0.2,
            ),
            patch("twomarkdown.batch.processor.conversion_config.parallel_workers", 1),
            patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
                side_effect=convert,
            ),
        ):
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
                show_progress=False,
            )

        assert result.failed == 1
        assert result.converted == 1
        assert any("timeout after" in (p or "") for p in result.failed_paths) or (
            str(hung.resolve()) in result.failed_paths
        )
        payload = json.loads(
            (output_dir / ".2markdown-manifest.json").read_text(encoding="utf-8")
        )
        hung_record = payload["files"][str(hung.resolve())]
        assert hung_record["status"] == "failed"
        assert "timeout" in (hung_record["error"] or "")

    def test_process_batch_times_out_parallel_file(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — parallel workers time out the hung file only."""
        input_dir, output_dir = batch_dirs
        hung = input_dir / "slow.txt"
        hung.write_text("slow")
        fast = input_dir / "fast.txt"
        fast.write_text("fast")

        def convert(path: Path) -> str:
            if path.name == "slow.txt":
                time.sleep(5)
            return "ok"

        with (
            patch(
                "twomarkdown.batch.processor.conversion_config.file_timeout_sec",
                0.2,
            ),
            patch("twomarkdown.batch.processor.conversion_config.parallel_workers", 2),
            patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
                side_effect=convert,
            ),
        ):
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
                show_progress=False,
            )

        assert result.failed == 1
        assert result.converted == 1
        assert str(hung.resolve()) in result.failed_paths

    def test_process_batch_parallel_timeout_does_not_expire_queued_files(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """Queued files must not inherit the in-flight files' 300s clock."""
        input_dir, output_dir = batch_dirs
        hung_paths = []
        fast_paths = []
        for name in ("slow-a.txt", "slow-b.txt"):
            path = input_dir / name
            path.write_text("slow")
            hung_paths.append(path)
        for name in ("fast-a.txt", "fast-b.txt", "fast-c.txt"):
            path = input_dir / name
            path.write_text("fast")
            fast_paths.append(path)

        def convert(path: Path) -> str:
            if path.name.startswith("slow"):
                time.sleep(1.2)
            return "ok"

        with (
            patch(
                "twomarkdown.batch.processor.conversion_config.file_timeout_sec",
                0.25,
            ),
            patch("twomarkdown.batch.processor.conversion_config.parallel_workers", 2),
            patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
                side_effect=convert,
            ),
        ):
            result = process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
                show_progress=False,
            )

        assert result.failed == 2
        assert result.converted == 3
        for path in hung_paths:
            assert str(path.resolve()) in result.failed_paths
        for path in fast_paths:
            assert (output_dir / path.with_suffix(".md").name).exists()

    def test_process_batch_manifest_has_first_file_before_second_converts(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """process_batch() — manifest is on disk after the first file is recorded."""
        input_dir, output_dir = batch_dirs
        first = input_dir / "a.txt"
        second = input_dir / "b.txt"
        first.write_text("one")
        second.write_text("two")
        seen_first = {"ok": False}

        def convert(path: Path) -> str:
            if path.name == "b.txt":
                payload = json.loads(
                    (output_dir / ".2markdown-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                record = payload["files"].get(str(first.resolve()))
                seen_first["ok"] = record is not None and record["status"] == "ok"
            return "converted"

        with (
            patch(
                "twomarkdown.converter.markitdown_converter.convert_file",
                side_effect=convert,
            ),
            patch(
                "twomarkdown.batch.processor.conversion_config.parallel_workers",
                1,
            ),
        ):
            process_batch(
                input_dir,
                output_dir,
                skip_existing=False,
                ocr_enabled=False,
                show_progress=False,
            )

        assert seen_first["ok"] is True

    def test_compose_pdf_does_not_swallow_cancel(self, tmp_path: Path) -> None:
        """_compose_pdf() — timeout cancel is not a soft compose miss."""
        doc = fitz.open()
        doc.new_page()
        pdf_path = tmp_path / "scan.pdf"
        doc.save(pdf_path)
        doc.close()
        cancel = threading.Event()
        cancel.set()

        with pytest.raises(ConversionError, match="cancelled"):
            _compose_pdf("markitdown", pdf_path, cancel=cancel)
