"""Settings live in twomarkdown/config.py; environment flags must not override them."""

from twomarkdown.config import ConversionConfig, LLMConfig, Secrets


class TestSettingsIgnoreEnv:
    """Settings come from code and `.ocr-mode`, never from the environment.

    These assert that an environment variable had no effect, rather than naming
    the value it failed to override. The shipped defaults are not a fixed target:
    `make build ollama` writes `.ocr-mode`, which legitimately changes the
    backend and the model, and asserting the out-of-the-box values made both of
    these fail on any machine where OCR had been set up.
    """

    def test_bool_flags_come_from_code(self, monkeypatch) -> None:
        monkeypatch.setenv("SKIP_EXISTING", "false")
        monkeypatch.setenv("CONVERT_EXISTING_MD", "true")
        cfg = ConversionConfig()
        assert cfg.skip_existing is True
        assert cfg.convert_existing_md is False

    def test_the_backend_is_not_read_from_the_environment(self, monkeypatch) -> None:
        before = ConversionConfig().ocr_backend
        monkeypatch.setenv("OCR_BACKEND", "an-engine-that-does-not-exist")

        assert ConversionConfig().ocr_backend == before

    def test_llm_flags_come_from_code(self, monkeypatch) -> None:
        before = LLMConfig()
        monkeypatch.setenv("LLM_ENABLED", "true" if not before.llm_enabled else "false")
        monkeypatch.setenv("OLLAMA_VISION_MODEL", "ollama:llava")
        cfg = LLMConfig()

        assert cfg.llm_enabled is before.llm_enabled
        assert cfg.vision_model == before.vision_model
        assert cfg.vision_model != "ollama:llava"

    def test_secrets_ignore_unknown_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SKIP_EXISTING", "false")
        Secrets()
