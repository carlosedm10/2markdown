"""2markdown — batch folder-to-markdown conversion.

Prefer ``import twomarkdown`` in application code. Internal modules stay ``src.*``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["BatchResult", "convert", "convert_batch", "convert_file"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from src import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
