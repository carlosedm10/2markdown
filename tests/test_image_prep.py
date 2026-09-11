"""Tests for vision-LLM image preparation."""

from io import BytesIO

from PIL import Image

from twomarkdown.converter.image_prep import prepare_image_for_vision_llm


def _large_png_bytes(width: int = 2400, height: int = 3000) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPrepareImageForVisionLlm:
    def test_downscales_oversized_image(self) -> None:
        raw = _large_png_bytes()
        prepared, mime = prepare_image_for_vision_llm(
            raw, max_dimension=1024, max_bytes=500_000
        )

        assert mime == "image/jpeg"
        assert len(prepared) < len(raw)
        with Image.open(BytesIO(prepared)) as img:
            assert max(img.size) <= 1024

    def test_leaves_small_png_unchanged(self) -> None:
        buf = BytesIO()
        Image.new("RGB", (64, 64), color=(200, 100, 50)).save(buf, format="PNG")
        raw = buf.getvalue()

        prepared, mime = prepare_image_for_vision_llm(
            raw,
            max_dimension=2048,
            max_bytes=500_000,
        )

        assert prepared == raw
        assert mime == "image/png"


class TestRasterizeSvg:
    def test_rasterize_svg_requires_extra(self, tmp_path) -> None:
        from pathlib import Path
        from unittest.mock import patch

        import pytest

        from twomarkdown.converter.image_prep import rasterize_svg

        svg = tmp_path / "icon.svg"
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        with patch.dict("sys.modules", {"cairosvg": None}):
            with pytest.raises(RuntimeError, match="EXTRA=svg"):
                rasterize_svg(Path(svg))

