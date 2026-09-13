"""YAML frontmatter for converted markdown files."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from twomarkdown.config import conversion_config, llm_config
from twomarkdown.converter.pdf_ocr import pdf_meta
from twomarkdown.language import guess_language

_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_TABLE_HEADING = re.compile(r"^### Table \(page \d+\)", re.MULTILINE)


def _ocr_pages(markdown: str) -> list[int]:
    pages: set[int] = set()
    current: int | None = None
    for line in (markdown or "").splitlines():
        match = re.match(r"^## Page (\d+)", line)
        if match:
            current = int(match.group(1))
            if "OCR" in line:
                pages.add(current)
            continue
        if current is not None and line.strip() in {"### OCR", "### [OCR]"}:
            pages.add(current)
    return sorted(pages)


def _yaml_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _title_from_markdown(markdown: str) -> str | None:
    match = _HEADING.search(markdown or "")
    if match:
        title = match.group(1).strip()
        return title or None
    return None


def _pdf_meta(source: Path) -> tuple[str | None, int | None]:
    if source.suffix.lower() != ".pdf":
        return None, None
    return pdf_meta(source)


def build_frontmatter(
    source: Path,
    input_dir: Path,
    markdown: str,
    *,
    source_rel: Path | None = None,
    ocr_fallback_pages: int = 0,
) -> str:
    if source_rel is None:
        try:
            rel = source.resolve().relative_to(input_dir.resolve())
        except ValueError:
            rel = Path(source.name)
    else:
        rel = source_rel
    backend = conversion_config.ocr_backend if conversion_config.ocr_enabled else "none"
    # "ollama" is only the engine family; it says nothing about which model ran,
    # and reads as wrong when the model is hosted. Record what actually did the
    # work, so a converted file is traceable to the model that produced it.
    ocr_model = ""
    if conversion_config.ocr_enabled and llm_config.llm_enabled:
        from twomarkdown.agents.image_ocr import normalize_model_id

        ocr_model = normalize_model_id(llm_config.vision_model)
        if ocr_model:
            backend = ocr_model.split(":", 1)[0]
    pdf_title, page_count = _pdf_meta(source)
    title = pdf_title or _title_from_markdown(markdown)
    ocr_pages = _ocr_pages(markdown)
    table_count = len(_TABLE_HEADING.findall(markdown or ""))
    language = guess_language(markdown)

    fields: list[tuple[str, object]] = [
        ("source", rel.as_posix()),
        ("converted_at", datetime.now(UTC).isoformat()),
        ("ocr_backend", backend),
        ("ocr_model", ocr_model or None),
        # Naming the model is only honest while the model did the work. Pages it
        # declined fall through to Tesseract, whose confident noise on handwriting
        # is indistinguishable from a transcription unless the file says so.
        ("ocr_fallback_pages", ocr_fallback_pages or None),
        ("title", title),
        ("page_count", page_count),
        ("language", language),
        ("ocr_pages", ocr_pages),
        ("tables", table_count),
        ("char_count", len(markdown or "")),
    ]
    lines = ["---"]
    for key, value in fields:
        if key == "ocr_pages":
            pages = value if isinstance(value, list) else []
            lines.append(f"ocr_pages: [{', '.join(str(p) for p in pages)}]")
            continue
        if value is None:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
