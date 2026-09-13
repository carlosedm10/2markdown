"""Optional Ollama vision OCR via Pydantic AI."""

from __future__ import annotations

import logging
import re
import threading
import time

import httpx
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider, infer_provider_class
from pydantic_ai.providers.ollama import OllamaProvider

from twomarkdown.config import llm_config
from twomarkdown.converter.image_prep import prepare_image_for_vision_llm
from twomarkdown.prompts import (
    FIGURE_DESCRIBE_PROMPT,
    IMAGE_OCR_PROMPT,
    figure_language_instruction,
)

logger = logging.getLogger(__name__)

# Agents are per thread, never module-level globals. `run_sync` drives each call on
# its own event loop, and an httpx.AsyncClient's pooled connections belong to the
# loop that opened them. A client shared across worker threads therefore hands a
# connection from a dead loop to the next caller: the request logs "200 OK", the
# OpenAI client retries it, and the batch stalls at 0% CPU holding the vision permit.
_local = threading.local()

# One permit per model for hosted providers, one shared permit for everything
# local. Two local models are not genuinely concurrent: they share one GPU, and
# overlapping requests make Ollama's OpenAI-compatible endpoint answer with a
# malformed completion (`role: ""`, which pydantic-ai rejects) instead of text.
# Measured: 4 of 6 pages failed with a figure model running alongside, 2 of them
# after burning 200s of GPU time first. Serialized, the same 6 pages all pass.
_page_ocr_lock = threading.Semaphore(1)
_figure_lock = threading.Semaphore(1)
_remote_lock = threading.Semaphore(llm_config.remote_max_concurrency)


def _permit_for(model: str, local_lock: threading.Semaphore) -> threading.Semaphore:
    """The right queue for a model: one permit locally, several for a hosted API.

    A single GPU serves one request at a time, so local work is serialized. A
    hosted provider has no such limit, and queueing against it would throw away
    most of the wall clock.
    """
    if not is_local_model(model):
        return _remote_lock
    return local_lock


def _page_permit() -> threading.Semaphore:
    return _permit_for(llm_config.vision_model, _page_ocr_lock)


def effective_figure_model() -> str:
    """The model that will actually caption figures.

    Two local models stay resident at once — 29.1 GB for the 32B page model plus
    8.8 GB for a 7B captioner, against the ~36 GB macOS leaves the GPU on a 48 GB
    machine. The image encoder is what runs out first: Ollama logs
    `kIOGPUCommandBufferCallbackErrorOutOfMemory` inside `clip_encode` and answers
    with a malformed completion. Reusing the page model keeps one model resident,
    and it is already loaded, so a caption costs inference only.
    """
    figure = llm_config.figure_model
    vision = llm_config.vision_model
    if figure and vision and is_local_model(figure) and is_local_model(vision):
        return vision
    return figure


def _figure_permit() -> threading.Semaphore:
    if is_local_model(llm_config.figure_model):
        # Any local model shares the page queue: one GPU serves one request at a
        # time, and asking it for two at once corrupts the reply rather than
        # queueing it. A different model name does not buy real concurrency.
        return _page_ocr_lock
    return _permit_for(llm_config.figure_model, _figure_lock)

# Small vision models fall into repetition loops on dense pages (gemma3:4b will
# repeat one equation until it is cut off). Greedy decoding plus a hard token
# ceiling bounds the damage and the wall-clock cost of a bad page.
_TRANSCRIBE_SETTINGS: dict[str, object] = {"temperature": 0.0, "max_tokens": 3072}
_DESCRIBE_SETTINGS: dict[str, object] = {"temperature": 0.0, "max_tokens": 512}


def strip_wrapping_fence(text: str) -> str:
    """Drop a code fence the model wrapped the whole answer in.

    Vision models return ```markdown ... ``` however firmly the prompt says not to;
    left in place it turns a whole page into a literal code block.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text.strip()
    lines = stripped.split("\n")
    if len(lines) < 2:
        return text.strip()
    # Only unwrap when the fence really encloses everything.
    if not lines[-1].strip().startswith("```"):
        return text.strip()
    inner = lines[1:-1]
    if any(line.lstrip().startswith("```") for line in inner):
        return text.strip()
    return "\n".join(inner).strip()


# The model sees a picture, not a filesystem, so any ![alt](path) it writes points
# at a file that does not exist. The pipeline adds the real figure links itself.
_INVENTED_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")


def strip_invented_image_links(text: str) -> str:
    """Drop markdown image links a vision model invented during transcription.

    Keeps the alt text when it carries a caption, so "![Figura 3](fig3.png)"
    degrades to "Figura 3" rather than vanishing.
    """

    def _replace(match: re.Match) -> str:
        alt = match.group(1).strip()
        return alt if alt else ""

    return _INVENTED_IMAGE_RE.sub(_replace, text)


# A 429 that waiting cannot fix. The provider uses the same status for "too fast"
# and "out of credit", but only the first is worth retrying: an exhausted balance
# cost 178s of backoff per page before giving up, which over a batch is an hour
# spent confirming the same answer.
_PERMANENT_QUOTA_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_hard_limit_reached",
    "no credits remaining",
    "exceeded your current quota",
)


def _is_out_of_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_QUOTA_MARKERS)


def _is_rate_limited(exc: Exception) -> bool:
    if _is_out_of_quota(exc):
        return False
    text = str(exc)
    return "429" in text or "rate limit" in text.lower()


def _is_transient_bad_response(exc: Exception) -> bool:
    """True for a reply that is malformed rather than wrong.

    Under concurrent load Ollama answers `/v1/chat/completions` with an empty
    `role`, which pydantic-ai rejects as a validation error. It says nothing
    about the page, so it deserves a retry rather than an empty transcription
    cached and passed off as what the model read.
    """
    text = str(exc)
    return "Invalid response from" in text or (
        "validation error" in text and "ChatCompletion" in text
    )


def _unload_local_model(model: str) -> bool:
    """Ask Ollama to drop a model, so the next call rebuilds its Metal backend.

    A GPU OOM inside the image encoder leaves llama.cpp reporting "backend is in
    error state from a previous command buffer failure - recreate the backend to
    recover": every later request on that model fails identically, so one bad
    page costs the rest of the run. Unloading is the documented way to recreate
    it. Best effort — a failure here only means the retry starts no worse off.
    """
    if not is_local_model(model):
        return False
    base = llm_config.ollama_base_url.rstrip("/").removesuffix("/v1")
    try:
        import httpx

        httpx.post(
            f"{base}/api/generate",
            json={"model": normalize_model_id(model).split(":", 1)[1], "keep_alive": 0},
            timeout=llm_config.connect_timeout_sec,
        )
    except Exception as exc:
        logger.debug("Could not unload %s: %s", model, exc)
        return False
    logger.info("Unloaded %s to recreate its GPU backend", model)
    return True


def _call_with_backoff(run, *, what: str, model: str = ""):
    """Retry a model call through rate limiting, with exponential backoff.

    A hosted provider answers 429 when a batch outruns its quota. Without this a
    whole run silently loses work: 139 figure captions were dropped in one pass,
    each as nothing more than a warning line.
    """
    delay = llm_config.rate_limit_initial_delay_sec
    for attempt in range(llm_config.rate_limit_max_retries + 1):
        try:
            return run()
        except Exception as exc:
            last = attempt == llm_config.rate_limit_max_retries
            if last or not (
                _is_rate_limited(exc) or _is_transient_bad_response(exc)
            ):
                raise
            logger.info(
                "%s got a transient failure, retrying in %.1fs (attempt %s/%s)",
                what,
                delay,
                attempt + 1,
                llm_config.rate_limit_max_retries,
            )
            if model and _is_transient_bad_response(exc):
                _unload_local_model(model)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _vision_http_client() -> httpx.AsyncClient:
    """HTTP client with an explicit read timeout.

    The default client waits indefinitely. Because every vision call holds the
    single vision permit, one stalled connection strands its
    worker and blocks every other worker behind it — the batch deadlocks with the
    process at 0% CPU. A read timeout turns that into one failed page.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            llm_config.request_timeout_sec,
            connect=llm_config.connect_timeout_sec,
        ),
        # No keep-alive: run_sync may open a fresh event loop per call, and a
        # pooled connection from the previous loop stalls the next request.
        limits=httpx.Limits(max_keepalive_connections=0),
    )


DEFAULT_PROVIDER = "ollama"


def normalize_model_id(model: str) -> str:
    """Return a "<provider>:<model>" id, defaulting a bare name to Ollama.

    pydantic-ai resolves the provider from the prefix, so "openai:gpt-5.2" and
    "anthropic:claude-sonnet-4-5" work with no extra code. A bare "qwen2.5vl:7b"
    is ambiguous — it already contains a colon — so anything whose prefix is not
    a known provider is treated as an Ollama model name.
    """
    model = (model or "").strip()
    if not model:
        return model
    prefix = model.split(":", 1)[0]
    try:
        infer_provider_class(prefix)
    except Exception:
        return f"{DEFAULT_PROVIDER}:{model}"
    return model


def model_provider(model: str) -> str:
    return normalize_model_id(model).split(":", 1)[0]


def is_local_model(model: str) -> bool:
    """Local models serve one request at a time; hosted APIs do not."""
    return model_provider(model) == DEFAULT_PROVIDER


def _provider_factory(provider_name: str):
    if provider_name == DEFAULT_PROVIDER:
        return OllamaProvider(
            base_url=llm_config.ollama_base_url,
            http_client=_vision_http_client(),
        )
    # Every other provider reads its own credentials from the environment.
    return infer_provider(provider_name)


def get_image_ocr_agent() -> Agent[None, str]:
    """Page-transcription agent for the calling thread."""
    agent = getattr(_local, "ocr_agent", None)
    if agent is None:
        model = infer_model(
            normalize_model_id(llm_config.vision_model),
            provider_factory=_provider_factory,
        )
        agent = Agent(
            model=model,
            system_prompt=IMAGE_OCR_PROMPT.strip(),
            output_type=str,
            model_settings=_TRANSCRIBE_SETTINGS,
        )
        _local.ocr_agent = agent
    return agent


def get_figure_agent() -> Agent[None, str]:
    """Figure-description agent for the calling thread."""
    agent = getattr(_local, "figure_agent", None)
    if agent is None:
        model = infer_model(
            normalize_model_id(effective_figure_model()),
            provider_factory=_provider_factory,
        )
        agent = Agent(
            model=model,
            system_prompt=FIGURE_DESCRIBE_PROMPT.strip(),
            output_type=str,
            model_settings=_DESCRIBE_SETTINGS,
        )
        _local.figure_agent = agent
    return agent


def describe_image_bytes_llm(
    image_bytes: bytes, *, mime_type: str = "image/png", language: str | None = None
) -> str:
    """Short figure caption using the configured Ollama vision model."""
    if not llm_config.llm_enabled:
        return ""

    prepared, mime_type = prepare_image_for_vision_llm(
        image_bytes,
        max_dimension=llm_config.llm_figure_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )
    agent = get_figure_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with _figure_permit():
            result = _call_with_backoff(
                lambda: agent.run_sync(
                    [
                        "Describe this figure for revision notes. "
                        + figure_language_instruction(language),
                        content,
                    ],
                ),
                what="Figure description",
                model=effective_figure_model(),
            )
        return (result.output or "").strip()
    except ModelAPIError as exc:
        logger.warning("Figure description API error: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Figure description failed: %s", exc)
        return ""


def ocr_image_bytes_llm(image_bytes: bytes, *, mime_type: str = "image/png") -> str:
    if not llm_config.llm_enabled:
        logger.warning("LLM OCR requested but llm_enabled is false")
        return ""

    prepared, mime_type = prepare_image_for_vision_llm(
        image_bytes,
        max_dimension=llm_config.llm_ocr_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )

    from twomarkdown.telemetry import span

    agent = get_image_ocr_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with _page_permit(), span("ocr.ollama"):
            result = _call_with_backoff(
                lambda: agent.run_sync(
                    ["Transcribe this page to Markdown.", content],
                ),
                what="Page OCR",
                model=llm_config.vision_model,
            )
        return strip_invented_image_links(strip_wrapping_fence(result.output or ""))
    except ModelAPIError as exc:
        logger.warning("Vision OCR API error: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Vision OCR failed: %s", exc)
        return ""
