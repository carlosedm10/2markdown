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

    def test_escape_applescript_string_escapes_quotes_and_backslashes(self) -> None:
        """_escape_applescript_string() — escapes backslashes and double quotes."""
        assert iwork._escape_applescript_string('a\\b"c') == 'a\\\\b\\"c'

    def test_convert_bundle_pages_uses_preview_pdf(self, tmp_path: Path) -> None:
        """convert_bundle() — Pages uses preview.pdf when text meets min_chars."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "preview.pdf").write_bytes(b"%PDF-1.4\n")
        long_preview_text = "x" * 50

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="IWA body text",
        ):
            markdown = iwork.convert_bundle(
                bundle,
                convert_pdf=lambda p: long_preview_text,
            )

        assert markdown == long_preview_text

    def test_convert_bundle_pages_falls_through_short_preview_to_iwa(
        self, tmp_path: Path
    ) -> None:
        """convert_bundle() — short preview.pdf text falls through to IWA extraction."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()
        (bundle / "preview.pdf").write_bytes(b"%PDF-1.4\n")

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="IWA body text",
        ) as mock_iwa:
            markdown = iwork.convert_bundle(
                bundle,
                convert_pdf=lambda p: "hi",
            )

        assert markdown == "IWA body text"
        mock_iwa.assert_called_once_with(bundle)

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

    def test_convert_bundle_pages_uses_preview_image_when_iwa_empty(
        self, tmp_path: Path
    ) -> None:
        """convert_bundle() — Pages without PDF/IWA OCR the preview image."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()
        (bundle / "preview.jpg").write_bytes(b"fake-jpeg")

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="",
        ):
            markdown = iwork.convert_bundle(
                bundle,
                convert_image=lambda p: "Preview OCR text",
            )

        assert markdown == "Preview OCR text"

    def test_convert_bundle_pages_tries_full_preview_after_empty_web(
        self, tmp_path: Path
    ) -> None:
        """convert_bundle() — empty preview-web.jpg does not skip preview.jpg."""
        bundle = tmp_path / "doc.pages"
        bundle.mkdir()
        (bundle / "Metadata").mkdir()
        (bundle / "preview-web.jpg").write_bytes(b"web")
        (bundle / "preview.jpg").write_bytes(b"full")

        def convert_image(path: Path) -> str:
            return "Full page OCR" if path.name == "preview.jpg" else ""

        with patch(
            "src.converter.iwork._extract_iwa_text_from_bundle",
            return_value="",
        ):
            markdown = iwork.convert_bundle(bundle, convert_image=convert_image)

        assert markdown == "Full page OCR"

    def test_convert_bundle_numbers_retries_isolated_on_proto_conflict(
        self, tmp_path: Path
    ) -> None:
        """convert_bundle() — Numbers protobuf pool clashes retry in a subprocess."""
        path = tmp_path / "sheet.numbers"
        path.write_bytes(b"not-a-real-numbers-file")

        with patch(
            "src.converter.iwork._numbers_document_to_markdown",
            side_effect=TypeError(
                "Couldn't build proto file into descriptor pool: "
                "duplicate file name TSDArchives.proto"
            ),
        ):
            with patch(
                "src.converter.iwork._numbers_document_to_markdown_isolated",
                return_value="## Sheet",
            ) as mock_isolated:
                markdown = iwork.convert_bundle(path)

        mock_isolated.assert_called_once_with(path)
        assert markdown == "## Sheet"
