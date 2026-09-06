"""Convert legacy Microsoft Office formats (.doc, .ppt) via LibreOffice."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class LegacyOfficeError(Exception):
    """Raised when legacy Office conversion cannot be performed."""


_LEGACY_SUFFIXES = frozenset({".doc", ".ppt"})


def is_legacy_office(path: Path) -> bool:
    return path.suffix.lower() in _LEGACY_SUFFIXES


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _html_to_text(html: str) -> str:
    try:
        from markdownify import markdownify as md
    except ImportError:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\s*>", "\n\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    return md(html, heading_style="ATX").strip()


def convert_legacy_office(path: Path) -> str:
    """Convert a legacy .doc or .ppt file to markdown text via LibreOffice."""
    soffice = _find_soffice()
    if soffice is None:
        raise LegacyOfficeError("LibreOffice not installed")

    path = path.resolve()
    suffix = path.suffix.lower()

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "html",
            "--outdir",
            str(out_dir),
            str(path),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise LegacyOfficeError(
                f"LibreOffice conversion failed for {suffix}: {detail}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LegacyOfficeError(
                f"LibreOffice conversion timed out for {suffix}"
            ) from exc

        html_files = sorted(out_dir.glob("*.html"))
        if not html_files:
            raise LegacyOfficeError(
                f"LibreOffice produced no HTML output for {path.name}"
            )

        html = html_files[0].read_text(encoding="utf-8", errors="replace")
        text = _html_to_text(html)
        if not text:
            raise LegacyOfficeError(f"legacy office conversion yielded no text: {path}")
        return text
