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

    def test_a_failed_page_is_not_cached(self, tmp_path: Path) -> None:
        """OcrCache — "" is what every OCR path returns on error, never an answer.

        Caching it made one bad night permanent: the retry was served the empty
        string, never called the model, and fell back to Tesseract noise.
        """
        cache = OcrCache(tmp_path / "cache", backend="ollama", model="qwen2.5vl:32b")
        cache.put(b"img", "")

        assert cache.get(b"img") is None
        assert not list((tmp_path / "cache").glob("*.txt"))

    def test_an_empty_entry_already_on_disk_reads_as_a_miss(
        self, tmp_path: Path
    ) -> None:
        """OcrCache — poisoned entries from earlier runs must not be trusted."""
        cache = OcrCache(tmp_path / "cache", backend="ollama", model="qwen2.5vl:32b")
        cache.put(b"img", "real text")
        next(iter((tmp_path / "cache").glob("*.txt"))).write_text("")

        assert cache.get(b"img") is None

    def test_a_real_transcription_still_round_trips(self, tmp_path: Path) -> None:
        """OcrCache — refusing failures must not refuse short real output."""
        cache = OcrCache(tmp_path / "cache", backend="ollama", model="qwen2.5vl:32b")
        cache.put(b"img", "0")

        assert cache.get(b"img") == "0"
