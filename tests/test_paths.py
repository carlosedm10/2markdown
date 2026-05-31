"""Test cases for output path resolution (src.paths)."""

from pathlib import Path

from src.paths import default_output_dir, normalize_batch_input


class TestDefaultOutputDir:
    """Test cases for default_output_dir()."""

    def test_default_output_is_sibling_with_2markdown_suffix(self) -> None:
        """default_output_dir() — <parent>/<name>_2markdown beside input folder."""
        input_dir = Path("/Users/me/Downloads/folder_to_process")
        expected = Path("/Users/me/Downloads/folder_to_process_2markdown")

        assert default_output_dir(input_dir) == expected

    def test_default_output_for_single_file_uses_stem(self) -> None:
        """default_output_dir() — <parent>/<stem>_2markdown beside input file."""
        input_file = Path("/Users/me/Downloads/report.pdf")
        expected = Path("/Users/me/Downloads/report_2markdown")

        assert default_output_dir(input_file) == expected


class TestNormalizeBatchInput:
    """Test cases for normalize_batch_input()."""

    def test_directory_input_walks_whole_tree(self, tmp_path: Path) -> None:
        """normalize_batch_input() — directory returns batch root and no file filter."""
        input_dir = tmp_path / "docs"
        input_dir.mkdir()
        batch_root, output_dir, only_files = normalize_batch_input(input_dir)

        assert batch_root == input_dir.resolve()
        assert output_dir == tmp_path / "docs_2markdown"
        assert only_files is None

    def test_file_input_converts_only_that_file(self, tmp_path: Path) -> None:
        """normalize_batch_input() — file returns parent root and single-file list."""
        input_file = tmp_path / "report.pdf"
        input_file.write_bytes(b"%PDF-1.4")
        batch_root, output_dir, only_files = normalize_batch_input(input_file)

        assert batch_root == tmp_path.resolve()
        assert output_dir == tmp_path / "report_2markdown"
        assert only_files == [input_file.resolve()]
