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

    def test_discover_files_skips_inside_iwork_bundle_and_finds_root(
        self, nested_batch_dirs: tuple[Path, Path]
    ) -> None:
        """discover_files() — discovers bundle root, not Data/*.png inside it."""
        input_dir, output_dir = nested_batch_dirs

        pages = input_dir / "doc.pages"
        pages.mkdir()
        (pages / "Metadata").mkdir()
        (pages / "Metadata" / "DocumentIdentifier").write_text("id")
        (pages / "Data").mkdir()
        (pages / "Data" / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")

        files = discover_files(input_dir, output_dir)

        assert [f.name for f in files] == ["doc.pages"]
        assert files[0].suffix == ".pages"
