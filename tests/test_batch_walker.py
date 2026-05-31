"""Test cases for folder discovery (src.batch.walker)."""

from pathlib import Path

from src.batch.walker import discover_files


class TestFolderWalker:
    """Test cases for discover_files()."""

    # -------------------------------------------------------------------------
    # Filtering
    # -------------------------------------------------------------------------

    def test_discover_files_skips_hidden_cache_and_output_tree(
        self, nested_batch_dirs: tuple[Path, Path]
    ) -> None:
        """discover_files() — skips dotfiles, __pycache__, and output tree."""
        input_dir, output_dir = nested_batch_dirs

        (input_dir / "visible.txt").write_text("hello")
        (input_dir / ".hidden.txt").write_text("secret")
        (input_dir / "__pycache__").mkdir()
        (input_dir / "__pycache__" / "cache.txt").write_text("cache")
        (output_dir / "inside_out.txt").write_text("skip")

        files = discover_files(input_dir, output_dir)
        expected_names = ["visible.txt"]

        assert [f.name for f in files] == expected_names

    # -------------------------------------------------------------------------
    # Ordering
    # -------------------------------------------------------------------------

    def test_discover_files_returns_stable_sorted_list(
        self, batch_dirs: tuple[Path, Path]
    ) -> None:
        """discover_files() — returns paths sorted by name for reproducible runs."""
        input_dir, output_dir = batch_dirs

        (input_dir / "b.txt").write_text("b")
        (input_dir / "a.txt").write_text("a")

        files = discover_files(input_dir, output_dir)
        expected_names = ["a.txt", "b.txt"]

        assert [f.name for f in files] == expected_names
