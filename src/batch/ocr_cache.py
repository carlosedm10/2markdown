"""Disk cache for OCR text keyed by SHA-256 of image bytes."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OcrCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, image_bytes: bytes) -> Path:
        digest = hashlib.sha256(image_bytes).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    def get(self, image_bytes: bytes) -> str | None:
        path = self._path(image_bytes)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("OCR cache read failed: %s", exc)
            return None

    def put(self, image_bytes: bytes, text: str) -> None:
        path = self._path(image_bytes)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.debug("OCR cache write failed: %s", exc)
