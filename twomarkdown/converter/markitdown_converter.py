"""MarkItDown-based local file conversion."""

import logging
from pathlib import Path

from markitdown import MarkItDown

from twomarkdown.config import markitdown_config

logger = logging.getLogger(__name__)

_converter: MarkItDown | None = None


class ConversionError(Exception):
    """Raised when conversion yields no usable content."""


def get_converter() -> MarkItDown:
    global _converter
    if _converter is None:
        _converter = MarkItDown(
            enable_plugins=markitdown_config.markitdown_enable_plugins,
        )
    return _converter


def convert_file(path: Path) -> str:
    """
    Convert a local file to markdown text via MarkItDown.convert_local().

    Returns an empty string when MarkItDown yields no content. ConversionError
    is raised by the batch processor for empty results, not by this function.
    Other errors propagate to the batch processor for soft-fail handling.
    """
    converter = get_converter()
    result = converter.convert_local(str(path.resolve()))
    text = (result.text_content or "").strip()
    return text
