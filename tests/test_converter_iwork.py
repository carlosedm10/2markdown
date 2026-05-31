"""Test cases for Apple iWork conversion (src.converter.iwork)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.converter import iwork
from src.converter.iwork import IWorkConversionError, _collect_text_from_obj


class TestIWorkConverter:
    """Test cases for iWork bundle helpers."""

    def test_collect_text_from_obj_extracts_string_and_list_text(self) -> None:
        """_collect_text_from_obj() — gathers text fields from nested IWA dicts."""
        data = {
            "chunks": [
                {"archives": [{"objects": [{"text": ["Hello", "World"]}]}]},
            ],
        }
        texts: list[str] = []
        _collect_text_from_obj(data, texts)
        assert texts == ["Hello", "World"]

    def test_is_iwork_bundle_true_for_pages_directory(self, tmp_path: Path) -> None:
        """is_iwork_bundle() — returns True for .pages directory bundles."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        assert iwork.is_iwork_bundle(bundle) is True

    def test_convert_bundle_pages_uses_preview_pdf(self, tmp_path: Path) -> None:
        """convert_bundle() — Pages uses preview.pdf via convert_pdf callback."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "preview.pdf").write_bytes(b"%PDF-1.4\n")

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="",
        ):
            markdown = iwork.convert_bundle(
                bundle,
                convert_pdf=lambda p: f"from-pdf:{p.name}",
            )

        assert markdown == "from-pdf:preview.pdf"

    def test_convert_bundle_pages_raises_when_no_content(self, tmp_path: Path) -> None:
        """convert_bundle() — Pages without preview or IWA text raises."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="",
        ):
            with pytest.raises(IWorkConversionError, match="preview.pdf"):
                iwork.convert_bundle(bundle)
