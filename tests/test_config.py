"""Settings live in twomarkdown/config.py; environment flags must not override them."""

from pathlib import Path

from twomarkdown.config import ConversionConfig, LLMConfig, Secrets, ollama_host


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


class TestOllamaHost:
    """B1: the desktop server (`make app`, native on the host) and the CLI's
    Docker container must resolve Ollama through the same rule, or the server
    confidently reports "Ollama · Listo" while every conversion call to it
    fails instantly and falls back to Tesseract without telling anyone."""

    def test_localhost_outside_a_container(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        assert ollama_host() == "localhost"

    def test_docker_internal_alias_inside_a_container(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "is_file", lambda self: True)
        assert ollama_host() == "host.docker.internal"

    def test_base_url_default_matches_the_host_rule(self) -> None:
        """`ollama_host()` is checked once at import (a process's container
        status never changes while it runs — see its docstring), so
        `LLMConfig`'s default must already be built from that same value,
        not a separately hard-coded host."""
        import twomarkdown.config as config_mod

        assert (
            LLMConfig().ollama_base_url == f"http://{config_mod._OLLAMA_HOST}:11434/v1"
        )


class TestRemoteMaxConcurrency:
    """M10: docs/desktop-app.md and the Pipeline mockup both say "4 permisos"
    for the cloud lane (`_remote_lock`) — the shipped default must agree."""

    def test_default_is_four(self) -> None:
        assert LLMConfig().remote_max_concurrency == 4
