"""Resize and compress images before vision-LLM OCR requests."""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def _input_mime_type(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare_image_for_vision_llm(
    image_bytes: bytes,
    *,
    max_dimension: int = 1568,
    max_bytes: int = 1_500_000,
    jpeg_quality: int = 85,
) -> tuple[bytes, str]:
    """
    Downscale and JPEG-compress image bytes for vision model APIs.

    Returns (bytes, mime_type). Skips re-encoding when already small enough.
    """
    if max_dimension < 1 or max_bytes < 1:
        raise ValueError("max_dimension and max_bytes must be positive")

    with Image.open(BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        width, height = img.size
        longest = max(width, height)
        if longest > max_dimension:
            scale = max_dimension / longest
            new_size = (
                max(1, int(width * scale)),
                max(1, int(height * scale)),
            )
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            logger.debug(
                "Resized image for vision OCR: %sx%s -> %sx%s",
                width,
                height,
                new_size[0],
                new_size[1],
            )

        input_mime = _input_mime_type(image_bytes)
        if (
            len(image_bytes) <= max_bytes
            and longest <= max_dimension
            and input_mime in ("image/jpeg", "image/png")
        ):
            return image_bytes, input_mime

        quality = jpeg_quality
        while quality >= 40:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= max_bytes:
                if quality < jpeg_quality:
                    logger.debug(
                        "Compressed image for vision OCR to %s bytes (quality=%s)",
                        len(out),
                        quality,
                    )
                return out, "image/jpeg"
            quality -= 15

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=40, optimize=True)
        out = buf.getvalue()
        logger.warning(
            "Vision OCR image still %s bytes after compression (limit %s)",
            len(out),
            max_bytes,
        )
        return out, "image/jpeg"
