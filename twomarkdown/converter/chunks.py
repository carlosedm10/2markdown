"""Split converted markdown into retrieval-friendly chunks."""

from __future__ import annotations

import json
import re
from pathlib import Path

from twomarkdown.config import conversion_config


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and "|" in stripped[1:]


def _is_table_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and all(_is_table_line(line) for line in lines)


def _split_blocks_preserving_tables(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_table = False

    for line in text.splitlines():
        table_line = _is_table_line(line)
        if table_line:
            if not in_table and current:
                blocks.append("\n".join(current).strip())
                current = []
            in_table = True
            current.append(line)
            continue

        if in_table:
            blocks.append("\n".join(current).strip())
            current = []
            in_table = False

        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue

        current.append(line)

    if current:
        blocks.append("\n".join(current).strip())

    return [block for block in blocks if block]


def _split_sections(text: str) -> list[tuple[str, str]]:
    if not text.strip():
        return []

    parts = re.split(r"(?m)^## ", text)
    sections: list[tuple[str, str]] = []

    if parts and parts[0].strip():
        sections.append(("", parts[0].strip()))

    for part in parts[1:]:
        if not part.strip():
            continue
        lines = part.splitlines()
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        sections.append((heading, body))

    return sections


def _chunk_body(heading: str, body: str, *, max_chars: int, overlap: int) -> list[dict]:
    prefix = f"## {heading}\n\n" if heading else ""
    full_text = f"{prefix}{body}".strip() if body or heading else prefix.strip()
    if len(full_text) <= max_chars:
        return [
            {
                "heading": heading,
                "text": full_text,
                "char_count": len(full_text),
            }
        ]

    chunks: list[dict] = []
    blocks = _split_blocks_preserving_tables(body)
    current_parts: list[str] = []
    current_len = len(prefix)

    def flush() -> None:
        nonlocal current_parts, current_len
        if not current_parts and not body:
            return
        chunk_body = "\n\n".join(current_parts).strip()
        chunk_text = f"{prefix}{chunk_body}".strip() if chunk_body else prefix.strip()
        chunks.append(
            {
                "heading": heading,
                "text": chunk_text,
                "char_count": len(chunk_text),
            }
        )
        current_parts = []
        current_len = len(prefix)

    for block in blocks:
        block_len = len(block) + (2 if current_parts else 0)
        if current_len + block_len <= max_chars:
            current_parts.append(block)
            current_len += block_len
            continue

        if current_parts:
            flush()

        if _is_table_block(block):
            chunk_text = f"{prefix}{block}".strip()
            chunks.append(
                {
                    "heading": heading,
                    "text": chunk_text,
                    "char_count": len(chunk_text),
                }
            )
            continue

        if len(prefix) + len(block) <= max_chars:
            current_parts = [block]
            current_len = len(prefix) + len(block)
            continue

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        para_buffer: list[str] = []
        para_len = len(prefix)

        for paragraph in paragraphs:
            if _is_table_block(paragraph):
                if para_buffer:
                    chunk_body = "\n\n".join(para_buffer)
                    chunk_text = f"{prefix}{chunk_body}".strip()
                    chunks.append(
                        {
                            "heading": heading,
                            "text": chunk_text,
                            "char_count": len(chunk_text),
                        }
                    )
                    para_buffer = []
                    para_len = len(prefix)
                chunk_text = f"{prefix}{paragraph}".strip()
                chunks.append(
                    {
                        "heading": heading,
                        "text": chunk_text,
                        "char_count": len(chunk_text),
                    }
                )
                continue

            extra = len(paragraph) + (2 if para_buffer else 0)
            if para_len + extra <= max_chars:
                para_buffer.append(paragraph)
                para_len += extra
                continue

            if para_buffer:
                chunk_body = "\n\n".join(para_buffer)
                chunk_text = f"{prefix}{chunk_body}".strip()
                chunks.append(
                    {
                        "heading": heading,
                        "text": chunk_text,
                        "char_count": len(chunk_text),
                    }
                )
                if overlap > 0 and chunk_body:
                    overlap_text = chunk_body[-overlap:]
                    para_buffer = [overlap_text, paragraph]
                    para_len = len(prefix) + len(overlap_text) + len(paragraph) + 2
                else:
                    para_buffer = [paragraph]
                    para_len = len(prefix) + len(paragraph)
                continue

            if len(prefix) + len(paragraph) <= max_chars:
                para_buffer = [paragraph]
                para_len = len(prefix) + len(paragraph)
            else:
                start = 0
                while start < len(paragraph):
                    piece = paragraph[start : start + max_chars - len(prefix)]
                    chunk_text = f"{prefix}{piece}".strip()
                    chunks.append(
                        {
                            "heading": heading,
                            "text": chunk_text,
                            "char_count": len(chunk_text),
                        }
                    )
                    if overlap <= 0:
                        start += len(piece)
                    else:
                        start = max(start + 1, start + len(piece) - overlap)

        if para_buffer:
            chunk_body = "\n\n".join(para_buffer)
            chunk_text = f"{prefix}{chunk_body}".strip()
            chunks.append(
                {
                    "heading": heading,
                    "text": chunk_text,
                    "char_count": len(chunk_text),
                }
            )

    if current_parts:
        flush()

    return chunks


def chunk_markdown(
    text: str,
    *,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> list[dict]:
    """Split markdown into chunks on headings without breaking GFM tables."""
    limit = max_chars if max_chars is not None else conversion_config.chunk_max_chars
    overlap_chars = overlap if overlap is not None else conversion_config.chunk_overlap

    sections = _split_sections(text)
    if not sections:
        return []

    chunks: list[dict] = []
    for heading, body in sections:
        chunks.extend(
            _chunk_body(
                heading,
                body,
                max_chars=limit,
                overlap=overlap_chars,
            )
        )
    return chunks


def write_chunks_sidecar(md_path: Path, chunks: list[dict]) -> Path:
    """Write chunk metadata next to a markdown file."""
    sidecar = md_path.with_suffix(".chunks.json")
    sidecar.write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    return sidecar
