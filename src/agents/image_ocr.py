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


def get_figure_agent() -> Agent[None, str]:
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


def describe_image_bytes_llm(
    image_bytes: bytes, *, mime_type: str = "image/png"
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
        result = agent.run_sync(
            ["Describe this document figure for indexing.", content],
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

    from src.telemetry import span

    agent = get_image_ocr_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with span("ocr.ollama"):
            result = agent.run_sync(
                [
                    "Extract all visible text from this image.",
                    content,
                ],
            )
        return (result.output or "").strip()
    except ModelAPIError as exc:
        logger.warning("Vision OCR API error: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("Vision OCR failed: %s", exc)
        return ""
