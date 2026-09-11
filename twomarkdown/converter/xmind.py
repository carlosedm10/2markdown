"""Convert XMind mind maps to nested Markdown lists.

An `.xmind` file is a zip whose `content.json` holds the topic tree verbatim, so the
outline converts losslessly — far better than rasterizing the exported PDF.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_DEPTH = 32


def is_xmind(path: Path) -> bool:
    return path.suffix.lower() == ".xmind" and path.is_file()


def _children(topic: dict[str, Any]) -> list[dict[str, Any]]:
    children = topic.get("children") or {}
    if not isinstance(children, dict):
        return []
    collected: list[dict[str, Any]] = []
    # "attached" is the normal branch; "detached" holds free-floating topics.
    for key in ("attached", "detached"):
        value = children.get(key)
        if isinstance(value, list):
            collected.extend(item for item in value if isinstance(item, dict))
    return collected


def _render_topic(topic: dict[str, Any], depth: int, out: list[str]) -> None:
    if depth > MAX_DEPTH:
        return
    title = str(topic.get("title") or "").strip()
    if title:
        out.append(f"{'  ' * depth}- {title}")
        note = topic.get("notes") or {}
        if isinstance(note, dict):
            plain = note.get("plain") or {}
            content = ""
            if isinstance(plain, dict):
                content = str(plain.get("content") or "").strip()
            if content:
                for line in content.splitlines():
                    if line.strip():
                        out.append(f"{'  ' * (depth + 1)}> {line.strip()}")
    for child in _children(topic):
        _render_topic(child, depth + 1 if title else depth, out)


def convert_xmind(path: Path) -> str:
    """Render every sheet's topic tree as a nested Markdown list."""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("content.json")
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        raise ValueError(f"unreadable xmind: {exc}") from exc

    try:
        sheets = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid xmind content.json: {exc}") from exc

    if isinstance(sheets, dict):
        sheets = [sheets]
    if not isinstance(sheets, list):
        raise ValueError("unexpected xmind content.json shape")

    parts: list[str] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        root = sheet.get("rootTopic")
        if not isinstance(root, dict):
            continue
        heading = str(root.get("title") or sheet.get("title") or path.stem).strip()
        lines: list[str] = [f"## {heading}", ""]
        for child in _children(root):
            _render_topic(child, 0, lines)
        if len(lines) > 2:
            parts.append("\n".join(lines))

    if not parts:
        raise ValueError("no topics found in xmind")
    return "\n\n".join(parts) + "\n"
