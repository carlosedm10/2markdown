"""Library API: twomarkdown.convert / callable module."""

from pathlib import Path
from unittest.mock import patch

import pytest

import twomarkdown
from src.api import convert, convert_batch, convert_file
from src.batch.processor import BatchResult
from src.config import conversion_config


class TestConvertFile:
    def test_returns_markdown_without_writing(self, tmp_path: Path) -> None:
        source = tmp_path / "note.txt"
        source.write_text("Hello library")
        sibling = tmp_path / "note_2markdown"
        with patch(
            "src.api.convert_file_to_markdown",
            return_value="Hello library",
        ) as mocked:
            text = convert_file(source, ocr=False)
        mocked.assert_called_once()
        assert text == "Hello library"
        assert not sibling.exists()
        assert not list(tmp_path.glob("*.md"))

    def test_bytes_need_suffix(self) -> None:
        with pytest.raises(ValueError, match="suffix"):
            convert(b"hello")

    def test_bytes_with_suffix(self) -> None:
        with patch(
            "src.api.convert_file_to_markdown",
            return_value="from bytes",
        ) as mocked:
            text = convert(b"hello", suffix=".txt", ocr=False)
        assert text == "from bytes"
        called_path: Path = mocked.call_args[0][0]
        assert called_path.suffix == ".txt"
        assert not called_path.exists()

    def test_restores_ocr_flag(self, tmp_path: Path) -> None:
        source = tmp_path / "note.txt"
        source.write_text("x")
        before = conversion_config.ocr_enabled
        with patch("src.api.convert_file_to_markdown", return_value="x"):
            convert(source, ocr=not before)
        assert conversion_config.ocr_enabled is before


class TestConvertBatch:
    def test_directory_returns_batch_result(self, batch_dirs: tuple[Path, Path]) -> None:
        input_dir, output_dir = batch_dirs
        (input_dir / "a.txt").write_text("A")
        with patch(
            "src.api.process_batch",
            return_value=BatchResult(converted=1),
        ) as mocked:
            result = convert(input_dir, output=output_dir, ocr=False)
        mocked.assert_called_once()
        assert isinstance(result, BatchResult)
        assert result.converted == 1

    def test_file_with_output_returns_written_markdown(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "doc.txt"
        source.write_text("body")
        out = tmp_path / "out"
        out.mkdir()
        written = out / "doc.md"
        written.write_text("---\nsource: doc.txt\n---\nbody\n")
        with patch(
            "src.api.process_batch",
            return_value=BatchResult(converted=1),
        ):
            text = convert(source, output=out, ocr=False)
        assert "body" in text
        assert isinstance(text, str)


class TestCallableModule:
    def test_module_call_matches_convert(self, tmp_path: Path) -> None:
        source = tmp_path / "note.txt"
        source.write_text("hi")
        with patch("src.api.convert_file_to_markdown", return_value="hi"):
            assert twomarkdown(source, ocr=False) == "hi"
            assert twomarkdown.convert(source, ocr=False) == "hi"
