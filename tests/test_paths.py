"""Test cases for output path resolution (src.paths)."""

from pathlib import Path

from src.paths import default_output_dir


class TestDefaultOutputDir:
    """Test cases for default_output_dir()."""

    def test_default_output_is_sibling_with_2markdown_suffix(self) -> None:
        """default_output_dir() — <parent>/<name>_2markdown beside input folder."""
        input_dir = Path("/Users/me/Downloads/folder_to_process")
        expected = Path("/Users/me/Downloads/folder_to_process_2markdown")

        assert default_output_dir(input_dir) == expected
