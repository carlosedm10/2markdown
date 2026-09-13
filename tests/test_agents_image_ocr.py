"""Post-processing of vision-model output."""

import pytest

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
            assert client.timeout.read == llm_config.request_timeout_sec
            assert client.timeout.connect == llm_config.connect_timeout_sec
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


class TestStripInventedImageLinks:
    def test_removes_a_made_up_image_link(self) -> None:
        """strip_invented_image_links() — a model-invented link is dropped."""
        from twomarkdown.agents.image_ocr import strip_invented_image_links

        out = strip_invented_image_links("Antes ![](convergencia_lineal.png) despues")
        assert "convergencia_lineal.png" not in out
        assert "Antes" in out and "despues" in out

    def test_keeps_alt_text_as_caption(self) -> None:
        """strip_invented_image_links() — alt text survives as plain text."""
        from twomarkdown.agents.image_ocr import strip_invented_image_links

        out = strip_invented_image_links("![Figura 3](fig3.png)")
        assert out.strip() == "Figura 3"

    def test_leaves_ordinary_text_alone(self) -> None:
        """strip_invented_image_links() — prose and maths are untouched."""
        from twomarkdown.agents.image_ocr import strip_invented_image_links

        text = "La funcion $f(x)$ es par. Ver [enlace](http://x) normal."
        assert strip_invented_image_links(text) == text


class TestSeparateModelQueues:
    """One GPU is one queue; a hosted provider is many."""

    def test_two_local_models_still_share_one_queue(self, monkeypatch) -> None:
        """_figure_permit() — distinct local names do not buy real concurrency.

        Overlapping requests for two Ollama models make its OpenAI-compatible
        endpoint answer with a malformed completion instead of text: 4 of 6 pages
        failed that way, 2 of them after 200s of GPU time. The name differs; the
        hardware does not.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:big")
        monkeypatch.setattr(llm_config, "figure_model", "ollama:small")
        assert image_ocr._figure_permit() is image_ocr._page_ocr_lock

    def test_permits_are_shared_when_models_match(self, monkeypatch) -> None:
        """_figure_permit() — one model means one queue, as before."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:same")
        monkeypatch.setattr(llm_config, "figure_model", "ollama:same")
        assert image_ocr._figure_permit() is image_ocr._page_ocr_lock

    def test_a_hosted_figure_model_keeps_its_own_queue(self, monkeypatch) -> None:
        """_figure_permit() — the serialization is about the GPU, not the models.

        A hosted provider serves many requests at once, so captions must not be
        made to wait behind local page transcription.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:big")
        monkeypatch.setattr(llm_config, "figure_model", "openai:gpt-4o-mini")
        assert image_ocr._figure_permit() is image_ocr._remote_lock


class TestProviderGate:
    """One setting selects any provider: "<provider>:<model>"."""

    def test_known_provider_prefix_is_kept(self) -> None:
        """normalize_model_id() — a real provider prefix passes through."""
        from twomarkdown.agents.image_ocr import normalize_model_id

        assert normalize_model_id("openai:gpt-5.2") == "openai:gpt-5.2"
        assert normalize_model_id("anthropic:claude-sonnet-4-5") == (
            "anthropic:claude-sonnet-4-5"
        )

    def test_bare_model_name_defaults_to_ollama(self) -> None:
        """normalize_model_id() — a local model name needs no prefix.

        "qwen2.5vl:7b" already contains a colon, so the prefix is only a
        provider when it names one.
        """
        from twomarkdown.agents.image_ocr import normalize_model_id

        assert normalize_model_id("qwen2.5vl:7b") == "ollama:qwen2.5vl:7b"
        assert normalize_model_id("ollama:qwen2.5vl:7b") == "ollama:qwen2.5vl:7b"

    def test_empty_model_is_left_alone(self) -> None:
        """normalize_model_id() — nothing in, nothing out."""
        from twomarkdown.agents.image_ocr import normalize_model_id

        assert normalize_model_id("") == ""

    def test_local_and_hosted_are_distinguished(self) -> None:
        """is_local_model() — only Ollama runs on our own GPU."""
        from twomarkdown.agents.image_ocr import is_local_model

        assert is_local_model("qwen2.5vl:32b") is True
        assert is_local_model("ollama:qwen2.5vl:32b") is True
        assert is_local_model("openai:gpt-5.2") is False

    def test_hosted_models_are_not_serialised(self, monkeypatch) -> None:
        """_page_permit() — a hosted API takes several requests at once."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "openai:gpt-5.2")
        assert image_ocr._page_permit() is image_ocr._remote_lock

    def test_local_model_keeps_one_permit(self, monkeypatch) -> None:
        """_page_permit() — one GPU, one request in flight."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        assert image_ocr._page_permit() is image_ocr._page_ocr_lock

    def test_same_local_model_shares_one_queue(self, monkeypatch) -> None:
        """_figure_permit() — captions on the page model must not double-load it."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:7b")
        monkeypatch.setattr(llm_config, "figure_model", "qwen2.5vl:7b")
        assert image_ocr._figure_permit() is image_ocr._page_ocr_lock


class TestImageBudget:
    def test_figures_are_sent_smaller_than_pages(self) -> None:
        """Config — captions use a smaller raster; token cost follows dimensions."""
        from twomarkdown.config import llm_config

        assert llm_config.llm_figure_max_dimension < llm_config.llm_ocr_max_dimension

    def test_figure_path_uses_the_figure_budget(self, monkeypatch) -> None:
        """describe_image_bytes_llm() — captions must not send page-sized images."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        seen: dict = {}

        def _fake_prepare(data, *, max_dimension, max_bytes, jpeg_quality):
            seen["max_dimension"] = max_dimension
            return data, "image/jpeg"

        class _Result:
            output = "una figura"

        class _Agent:
            def run_sync(self, *_args, **_kwargs):
                return _Result()

        monkeypatch.setattr(image_ocr, "prepare_image_for_vision_llm", _fake_prepare)
        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(image_ocr, "get_figure_agent", _Agent)

        assert image_ocr.describe_image_bytes_llm(b"\xff\xd8\xffdata") == "una figura"

        assert seen["max_dimension"] == llm_config.llm_figure_max_dimension


class TestRateLimitBackoff:
    def test_retries_then_succeeds(self, monkeypatch) -> None:
        """_call_with_backoff() — a 429 is waited out, not dropped."""
        from twomarkdown.agents import image_ocr

        monkeypatch.setattr(image_ocr.time, "sleep", lambda _s: None)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("status_code: 429, rate limit reached")
            return "ok"

        assert image_ocr._call_with_backoff(flaky, what="test") == "ok"
        assert calls["n"] == 3

    def test_non_rate_limit_errors_are_not_retried(self, monkeypatch) -> None:
        """_call_with_backoff() — only rate limiting is worth waiting for."""
        import pytest

        from twomarkdown.agents import image_ocr

        monkeypatch.setattr(image_ocr.time, "sleep", lambda _s: None)
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("malformed image")

        with pytest.raises(ValueError):
            image_ocr._call_with_backoff(broken, what="test")
        assert calls["n"] == 1

    def test_gives_up_after_the_configured_retries(self, monkeypatch) -> None:
        """_call_with_backoff() — persistent limiting surfaces, never loops forever."""
        import pytest

        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(image_ocr.time, "sleep", lambda _s: None)
        monkeypatch.setattr(llm_config, "rate_limit_max_retries", 2)
        calls = {"n": 0}

        def always_limited():
            calls["n"] += 1
            raise RuntimeError("429 Too Many Requests")

        with pytest.raises(RuntimeError):
            image_ocr._call_with_backoff(always_limited, what="test")
        assert calls["n"] == 3

    def test_detects_rate_limit_wording(self) -> None:
        """_is_rate_limited() — matches both the status code and the message."""
        from twomarkdown.agents.image_ocr import _is_rate_limited

        assert _is_rate_limited(RuntimeError("status_code: 429")) is True
        assert _is_rate_limited(RuntimeError("Rate limit reached")) is True
        assert _is_rate_limited(RuntimeError("connection reset")) is False


class TestTransientBadResponse:
    """A malformed reply is not a verdict on the page."""

    def test_malformed_completion_is_retried(self, monkeypatch) -> None:
        """_call_with_backoff() — Ollama's empty `role` earns another attempt.

        Without this the page was transcribed as "", cached as though the model
        had read it, and served on every later run.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(
                    "Invalid response from ollama chat completions endpoint: "
                    "1 validation error for ChatCompletion"
                )
            return "transcribed"

        assert image_ocr._call_with_backoff(flaky, what="Page OCR") == "transcribed"
        assert len(calls) == 2

    def test_a_real_error_still_raises(self, monkeypatch) -> None:
        """_call_with_backoff() — retrying a genuine failure only wastes the GPU."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        calls = []

        def broken():
            calls.append(1)
            raise ValueError("model not found")

        with pytest.raises(ValueError):
            image_ocr._call_with_backoff(broken, what="Page OCR")
        assert len(calls) == 1


class TestOneResidentLocalModel:
    """Two local models do not fit one GPU; the second one OOMs the encoder."""

    def test_a_local_figure_model_reuses_the_page_model(self, monkeypatch) -> None:
        """effective_figure_model() — one resident model, not two.

        29.1 GB for the page model plus 8.8 GB for a separate captioner exceeds
        what macOS leaves the GPU on a 48 GB machine, and Ollama fails inside
        `clip_encode` rather than queueing.
        """
        from twomarkdown.agents.image_ocr import effective_figure_model
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        monkeypatch.setattr(llm_config, "figure_model", "ollama:qwen2.5vl:7b")

        assert effective_figure_model() == "ollama:qwen2.5vl:32b"

    def test_a_hosted_figure_model_is_left_alone(self, monkeypatch) -> None:
        """effective_figure_model() — the constraint is the GPU, not the setting.

        A hosted captioner costs the local GPU nothing, so a small cheap model
        must stay available for captions.
        """
        from twomarkdown.agents.image_ocr import effective_figure_model
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "ollama:qwen2.5vl:32b")
        monkeypatch.setattr(llm_config, "figure_model", "openai:gpt-4o-mini")

        assert effective_figure_model() == "openai:gpt-4o-mini"

    def test_a_hosted_page_model_keeps_a_local_captioner(self, monkeypatch) -> None:
        """effective_figure_model() — nothing local is resident to collide with."""
        from twomarkdown.agents.image_ocr import effective_figure_model
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "vision_model", "openai:gpt-4o")
        monkeypatch.setattr(llm_config, "figure_model", "ollama:qwen2.5vl:7b")

        assert effective_figure_model() == "ollama:qwen2.5vl:7b"


class TestPoisonedBackendRecovery:
    """An OOM'd Metal backend never recovers on its own."""

    def test_a_malformed_reply_unloads_the_model_before_retrying(
        self, monkeypatch
    ) -> None:
        """_call_with_backoff() — recreate the backend, do not just wait.

        llama.cpp reports "backend is in error state ... recreate the backend to
        recover", so retrying against the same loaded model repeats the failure
        for every remaining page of the run.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        unloaded: list[str] = []
        monkeypatch.setattr(
            image_ocr,
            "_unload_local_model",
            lambda m: unloaded.append(m) or True,
        )
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError(
                    "Invalid response from ollama chat completions endpoint"
                )
            return "transcribed"

        out = image_ocr._call_with_backoff(
            flaky, what="Page OCR", model="ollama:qwen2.5vl:32b"
        )

        assert out == "transcribed"
        assert unloaded == ["ollama:qwen2.5vl:32b"]

    def test_rate_limiting_does_not_unload_anything(self, monkeypatch) -> None:
        """_call_with_backoff() — a 429 is a quota, not a broken backend.

        Reloading 29 GB to answer a hosted provider's rate limit would turn a
        one-second wait into a minute of dead GPU.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        unloaded: list[str] = []
        monkeypatch.setattr(
            image_ocr, "_unload_local_model", lambda m: unloaded.append(m) or True
        )
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("429 rate limit exceeded")
            return "ok"

        image_ocr._call_with_backoff(flaky, what="Page OCR", model="openai:gpt-4o")

        assert unloaded == []


class TestQuotaExhaustion:
    """A 429 the provider will keep answering is not worth waiting out."""

    def test_no_credit_is_not_retried(self, monkeypatch) -> None:
        """_call_with_backoff() — an exhausted balance fails the same way forever.

        It shares the 429 status with ordinary rate limiting, and backing off
        through it cost 178s per page before the call gave up anyway.
        """
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        calls = []

        def broke():
            calls.append(1)
            raise RuntimeError(
                "status_code: 429, body: {'type': 'insufficient_quota', "
                "'code': 'credit_balance_exhausted'}"
            )

        with pytest.raises(RuntimeError):
            image_ocr._call_with_backoff(broke, what="Page review")

        assert len(calls) == 1

    def test_ordinary_rate_limiting_is_still_retried(self, monkeypatch) -> None:
        """_call_with_backoff() — a real rate limit clears if you wait."""
        from twomarkdown.agents import image_ocr
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "rate_limit_initial_delay_sec", 0)
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("429 Too Many Requests: rate limit reached")
            return "ok"

        assert image_ocr._call_with_backoff(flaky, what="Page OCR") == "ok"
        assert len(calls) == 2
