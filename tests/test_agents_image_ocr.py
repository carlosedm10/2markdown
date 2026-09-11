"""Post-processing of vision-model output."""

from twomarkdown.agents.image_ocr import strip_wrapping_fence


class TestStripWrappingFence:
    def test_unwraps_a_markdown_fence(self) -> None:
        """strip_wrapping_fence() — a whole-answer fence is removed."""
        assert strip_wrapping_fence("```markdown\n# Tema 3\n\ntexto\n```") == (
            "# Tema 3\n\ntexto"
        )

    def test_unwraps_a_bare_fence(self) -> None:
        """strip_wrapping_fence() — a fence without a language tag is removed."""
        assert strip_wrapping_fence("```\nhola\n```") == "hola"

    def test_keeps_inner_code_blocks(self) -> None:
        """strip_wrapping_fence() — a real code block in the page is preserved."""
        text = "Ejemplo:\n\n```c\nint main();\n```"
        assert strip_wrapping_fence(text) == text

    def test_leaves_unfenced_text_alone(self) -> None:
        """strip_wrapping_fence() — ordinary output is returned trimmed."""
        assert strip_wrapping_fence("  # Titulo\n\ncuerpo  ") == "# Titulo\n\ncuerpo"

    def test_handles_empty_output(self) -> None:
        """strip_wrapping_fence() — empty stays empty."""
        assert strip_wrapping_fence("") == ""


class TestVisionHttpClient:
    def test_client_has_explicit_timeouts(self) -> None:
        """_vision_http_client() — explicit timeouts; a stall cannot deadlock."""
        from twomarkdown.agents.image_ocr import _vision_http_client
        from twomarkdown.config import llm_config

        client = _vision_http_client()
        try:
            assert client.timeout.read == llm_config.ollama_request_timeout_sec
            assert client.timeout.connect == llm_config.ollama_connect_timeout_sec
            assert client.timeout.read is not None
        finally:
            pass


class TestThreadLocalAgents:
    def test_each_thread_gets_its_own_agent(self) -> None:
        """get_image_ocr_agent() — agents are per thread, not shared globals.

        A shared Agent means a shared httpx.AsyncClient whose pooled connections
        belong to another thread's event loop; reusing one stalls the batch.
        """
        from concurrent.futures import ThreadPoolExecutor

        from twomarkdown.agents.image_ocr import get_image_ocr_agent

        with ThreadPoolExecutor(max_workers=3) as pool:
            agents = list(pool.map(lambda _: id(get_image_ocr_agent()), range(3)))

        assert len(set(agents)) == 3, "agent instance leaked across threads"

    def test_same_thread_reuses_its_agent(self) -> None:
        """get_image_ocr_agent() — one agent per thread, built once."""
        from twomarkdown.agents.image_ocr import get_image_ocr_agent

        assert get_image_ocr_agent() is get_image_ocr_agent()

    def test_client_disables_keepalive(self) -> None:
        """_vision_http_client() — no pooled connections to outlive their loop."""
        from twomarkdown.agents.image_ocr import _vision_http_client

        client = _vision_http_client()
        pool = client._transport._pool
        assert pool._max_keepalive_connections == 0
