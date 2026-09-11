"""Test cases for OCR disk cache (twomarkdown.batch.ocr_cache)."""

from pathlib import Path

from twomarkdown.batch.ocr_cache import OcrCache


class TestOcrCache:
    def test_same_bytes_different_backend_is_a_miss(self, tmp_path: Path) -> None:
        """OcrCache — backend is part of the key; switching engines misses."""
        image = b"\x89PNG fake"
        tess = OcrCache(tmp_path / "cache", backend="tesseract", lang="eng+spa")
        tess.put(image, "tesseract text")

        ollama = OcrCache(
            tmp_path / "cache",
            backend="ollama",
            lang="eng+spa",
            model="ollama:moondream",
        )
        assert ollama.get(image) is None
        assert tess.get(image) == "tesseract text"

    def test_same_backend_hits(self, tmp_path: Path) -> None:
        cache = OcrCache(tmp_path / "cache", backend="tesseract", lang="eng")
        cache.put(b"img", "hello")
        assert cache.get(b"img") == "hello"
