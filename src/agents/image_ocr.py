"""Optional Ollama vision OCR via Pydantic AI."""

from __future__ import annotations

import logging

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider
from pydantic_ai.providers.ollama import OllamaProvider

from src.config import llm_config
from src.converter.image_prep import prepare_image_for_vision_llm
from src.prompts import FIGURE_DESCRIBE_PROMPT, IMAGE_OCR_PROMPT

logger = logging.getLogger(__name__)

_agent: Agent[None, str] | None = None
_describe_agent: Agent[None, str] | None = None


def _ollama_provider_factory(provider_name: str):
    if provider_name == "ollama":
        return OllamaProvider(base_url=llm_config.ollama_base_url)
    return infer_provider(provider_name)


def get_image_ocr_agent() -> Agent[None, str]:
    global _agent
    if _agent is None:
        model = infer_model(
            llm_config.ollama_vision_model,
            provider_factory=_ollama_provider_factory,
        )
        _agent = Agent(
            model=model,
            system_prompt=IMAGE_OCR_PROMPT.strip(),
            output_type=str,
        )
    return _agent


def get_figure_describe_agent() -> Agent[None, str]:
    global _describe_agent
    if _describe_agent is None:
        model = infer_model(
            llm_config.ollama_vision_model,
            provider_factory=_ollama_provider_factory,
        )
        _describe_agent = Agent(
            model=model,
            system_prompt=FIGURE_DESCRIBE_PROMPT.strip(),
            output_type=str,
        )
    return _describe_agent


def _run_vision_agent(
    agent: Agent[None, str],
    image_bytes: bytes,
    *,
    mime_type: str,
    user_prompt: str,
    failure_label: str,
) -> str:
    if not llm_config.llm_enabled:
        logger.warning("%s requested but llm_enabled is false", failure_label)
        return ""

    prepared, mime_type = prepare_image_for_vision_llm(
        image_bytes,
        max_dimension=llm_config.llm_ocr_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )

    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        result = agent.run_sync([user_prompt, content])
        return (result.output or "").strip()
    except ModelAPIError as exc:
        logger.warning("%s API error: %s", failure_label, exc)
        return ""
    except Exception as exc:
        logger.warning("%s failed: %s", failure_label, exc)
        return ""


def ocr_image_bytes_llm(image_bytes: bytes, *, mime_type: str = "image/png") -> str:
    """Extract text from image bytes using the configured Ollama vision model."""
    return _run_vision_agent(
        get_image_ocr_agent(),
        image_bytes,
        mime_type=mime_type,
        user_prompt="Extract all visible text from this image.",
        failure_label="Vision OCR",
    )


def describe_image_bytes_llm(image_bytes: bytes, *, mime_type: str = "image/png") -> str:
    """Describe figure content for indexing when OCR yields no text."""
    return _run_vision_agent(
        get_figure_describe_agent(),
        image_bytes,
        mime_type=mime_type,
        user_prompt="Describe this figure for document indexing.",
        failure_label="Figure description",
    )
