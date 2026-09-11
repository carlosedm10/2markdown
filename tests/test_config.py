"""Settings live in twomarkdown/config.py; environment flags must not override them."""

from twomarkdown.config import ConversionConfig, LLMConfig, Secrets


class TestSettingsIgnoreEnv:
    def test_bool_flags_come_from_code(self, monkeypatch) -> None:
        monkeypatch.setenv("SKIP_EXISTING", "false")
        monkeypatch.setenv("OCR_BACKEND", "ollama")
        monkeypatch.setenv("CONVERT_EXISTING_MD", "true")
        cfg = ConversionConfig()
        assert cfg.skip_existing is True
        assert cfg.ocr_backend == "tesseract"
        assert cfg.convert_existing_md is False

    def test_llm_flags_come_from_code(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_ENABLED", "true")
        monkeypatch.setenv("OLLAMA_VISION_MODEL", "ollama:llava")
        cfg = LLMConfig()
        assert cfg.llm_enabled is False
        assert cfg.ollama_vision_model == "ollama:moondream"

    def test_secrets_ignore_unknown_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SKIP_EXISTING", "false")
        Secrets()
