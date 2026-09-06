"""Test OCR backend flag resolution (src.cli.resolve_ocr_backend)."""

from src.cli import resolve_ocr_backend


class TestResolveOcrBackend:
    """Test cases for resolve_ocr_backend()."""

    def test_ollama_flag_sets_backend_and_llm_enabled(self) -> None:
        """resolve_ocr_backend() — --ollama implies ollama backend and LLM."""
        backend, llm_enabled = resolve_ocr_backend(
            ollama=True,
            ocr_backend="tesseract",
            llm_enabled=False,
        )
        assert backend == "ollama"
        assert llm_enabled is True

    def test_ocr_backend_ollama_enables_llm_without_flag(self) -> None:
        """resolve_ocr_backend() — --ocr-backend=ollama enables LLM without --ollama."""
        backend, llm_enabled = resolve_ocr_backend(
            ollama=False,
            ocr_backend="ollama",
            llm_enabled=False,
        )
        assert backend == "ollama"
        assert llm_enabled is True

    def test_tesseract_backend_without_llm(self) -> None:
        """resolve_ocr_backend() — tesseract backend leaves llm_enabled false."""
        backend, llm_enabled = resolve_ocr_backend(
            ollama=False,
            ocr_backend="tesseract",
            llm_enabled=False,
        )
        assert backend == "tesseract"
        assert llm_enabled is False

    def test_tesseract_backend_with_llm_enabled_alias(self) -> None:
        """resolve_ocr_backend() — --llm-enabled still allowed with tesseract."""
        backend, llm_enabled = resolve_ocr_backend(
            ollama=False,
            ocr_backend="tesseract",
            llm_enabled=True,
        )
        assert backend == "tesseract"
        assert llm_enabled is True
