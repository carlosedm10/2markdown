"""Public import name for 2markdown. (`import 2markdown` is invalid Python.)"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

from src.api import BatchResult, convert, convert_batch, convert_file
from src.batch.processor import convert_file_to_markdown
from src.converter.markitdown_converter import ConversionError

__all__ = [
    "BatchResult",
    "ConversionError",
    "convert",
    "convert_batch",
    "convert_file",
    "convert_file_to_markdown",
]


class _CallableModule(ModuleType):
    def __call__(
        self,
        source: str | bytes | Any,
        /,
        output: str | Any | None = None,
        **kwargs: Any,
    ) -> Any:
        return convert(source, output, **kwargs)


sys.modules[__name__].__class__ = _CallableModule
