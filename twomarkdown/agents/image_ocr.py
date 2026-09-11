"""Optional Ollama vision OCR via Pydantic AI."""

from __future__ import annotations

import logging
import re
import threading

import httpx
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider
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

# Host Ollama is one model; parallel PDF workers must not stampede it.
_ollama_vision_lock = threading.Semaphore(1)

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


def _vision_http_client() -> httpx.AsyncClient:
    """HTTP client with an explicit read timeout.

    The default client waits indefinitely. Because every vision call holds the
    single ``_ollama_vision_lock`` permit, one stalled connection strands its
    worker and blocks every other worker behind it — the batch deadlocks with the
    process at 0% CPU. A read timeout turns that into one failed page.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            llm_config.ollama_request_timeout_sec,
            connect=llm_config.ollama_connect_timeout_sec,
        ),
        # No keep-alive: run_sync may open a fresh event loop per call, and a
        # pooled connection from the previous loop stalls the next request.
        limits=httpx.Limits(max_keepalive_connections=0),
    )


def _ollama_provider_factory(provider_name: str):
    if provider_name == "ollama":
        return OllamaProvider(
            base_url=llm_config.ollama_base_url,
            http_client=_vision_http_client(),
        )
    return infer_provider(provider_name)


def get_image_ocr_agent() -> Agent[None, str]:
    """Page-transcription agent for the calling thread."""
    agent = getattr(_local, "ocr_agent", None)
    if agent is None:
        model = infer_model(
            llm_config.ollama_vision_model,
            provider_factory=_ollama_provider_factory,
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
            llm_config.ollama_vision_model,
            provider_factory=_ollama_provider_factory,
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
        max_dimension=llm_config.llm_ocr_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )
    agent = get_figure_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with _ollama_vision_lock:
            result = agent.run_sync(
                [
                    "Describe this figure for revision notes. "
                    + figure_language_instruction(language),
                    content,
                ],
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
        with _ollama_vision_lock, span("ocr.ollama"):
            result = agent.run_sync(
                [
                    "Transcribe this page to Markdown.",
                    content,
                ],
            )
        return strip_invented_image_links(strip_wrapping_fence(result.output or ""))
    except ModelAPIError as exc:
        logger.warning("Vision OCR API error: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Vision OCR failed: %s", exc)
        return ""
