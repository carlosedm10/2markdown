"""Optional Ollama vision OCR via Pydantic AI."""

from __future__ import annotations

import logging

from pydantic_ai import Agent, BinaryContent

from src.config import llm_config
from src.prompts import IMAGE_OCR_PROMPT

logger = logging.getLogger(__name__)

_agent: Agent[None, str] | None = None


def get_image_ocr_agent() -> Agent[None, str]:
    global _agent
    if _agent is None:
        _agent = Agent(
            model=llm_config.ollama_vision_model,
            system_prompt=IMAGE_OCR_PROMPT.strip(),
            output_type=str,
        )
    return _agent


def ocr_image_bytes_llm(image_bytes: bytes, *, mime_type: str = "image/png") -> str:
    """Extract text from image bytes using the configured Ollama vision model."""
    if not llm_config.llm_enabled:
        logger.warning("LLM OCR requested but llm_enabled is false")
        return ""

    agent = get_image_ocr_agent()
    content = BinaryContent(data=image_bytes, media_type=mime_type)
    result = agent.run_sync(
        [
            "Extract all visible text from this image.",
            content,
        ],
    )
    return (result.output or "").strip()
