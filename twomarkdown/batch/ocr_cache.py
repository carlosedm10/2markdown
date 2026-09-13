"""Disk cache for OCR text keyed by backend, language, model, and image bytes."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OcrCache:
    def __init__(
        self,
        cache_dir: Path,
        *,
        backend: str = "",
        lang: str = "",
        model: str = "",
    ):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._key_prefix = f"{backend}|{lang}|{model}|".encode()

    def _path(self, image_bytes: bytes) -> Path:
        digest = hashlib.sha256(self._key_prefix + image_bytes).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    def get(self, image_bytes: bytes) -> str | None:
        """Cached text, or None when there is nothing worth reusing.

        An empty entry is reported as a miss. Entries written before `put` learned
        to refuse them are still on disk, and one of those is indistinguishable
        from a page the model genuinely read as blank — retrying it costs a model
        call, while trusting it costs the page.
        """
        path = self._path(image_bytes)
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("OCR cache read failed: %s", exc)
            return None
        return text or None

    def put(self, image_bytes: bytes, text: str) -> None:
        """Store a transcription. Empty output is a failure, not a result.

        Every OCR entry point returns "" when the model errors or times out, so
        caching it would make one bad night permanent: the retry is served the
        empty string, skips the model, and falls back to Tesseract noise.
        """
        if not text:
            return
        path = self._path(image_bytes)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.debug("OCR cache write failed: %s", exc)
