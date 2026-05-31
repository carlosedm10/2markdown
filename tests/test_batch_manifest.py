"""Test cases for conversion manifest (src.batch.manifest)."""

import json
from pathlib import Path

from src.batch.manifest import Manifest


class TestManifest:
    """Test cases for Manifest resume and persistence."""

    # -------------------------------------------------------------------------
    # Skip existing
    # -------------------------------------------------------------------------

    def test_should_skip_when_output_md_is_newer_than_source(
        self, tmp_path: Path
    ) -> None:
        """should_skip() — returns True when output .md mtime >= source mtime."""
        source = tmp_path / "doc.pdf"
        output_md = tmp_path / "doc.md"
        source.write_bytes(b"%PDF-1.4")
        output_md.write_text("# ok")

        manifest = Manifest(tmp_path / ".2markdown-manifest.json")

        assert manifest.should_skip(source, output_md, skip_existing=True) is True

    def test_should_not_skip_when_output_missing(self, tmp_path: Path) -> None:
        """should_skip() — returns False when output .md does not exist."""
        source = tmp_path / "doc.pdf"
        output_md = tmp_path / "doc.md"
        source.write_bytes(b"%PDF-1.4")

        manifest = Manifest(tmp_path / ".2markdown-manifest.json")

        assert manifest.should_skip(source, output_md, skip_existing=True) is False

    def test_should_not_skip_when_force_mode(self, tmp_path: Path) -> None:
        """should_skip() — returns False when skip_existing is False."""
        source = tmp_path / "doc.pdf"
        output_md = tmp_path / "doc.md"
        source.write_bytes(b"%PDF-1.4")
        output_md.write_text("# ok")

        manifest = Manifest(tmp_path / ".2markdown-manifest.json")

        assert manifest.should_skip(source, output_md, skip_existing=False) is False

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def test_record_and_save_persists_file_status(self, tmp_path: Path) -> None:
        """record() + save() — writes ok status keyed by resolved source path."""
        manifest_path = tmp_path / ".2markdown-manifest.json"
        source = tmp_path / "a.txt"
        source.write_text("x")
        output = tmp_path / "a.md"

        manifest = Manifest(manifest_path)
        manifest.record(source, status="ok", output=output)
        manifest.save()

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = str(source.resolve())
        expected_record = {
            "source": key,
            "status": "ok",
            "mtime": source.stat().st_mtime,
            "error": None,
            "output": str(output.resolve()),
        }

        assert key in payload["files"]
        assert payload["files"][key] == expected_record

        reloaded = Manifest(manifest_path)
        assert reloaded.records[key].status == "ok"
