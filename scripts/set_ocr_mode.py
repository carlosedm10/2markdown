#!/usr/bin/env python3
"""Persist OCR mode in .ocr-mode (stdlib only — runs on the host before Docker)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OCR_MODE_PATH = ROOT / ".ocr-mode"


def _normalize_vision_model(vision_model: str | None) -> str | None:
    if not vision_model:
        return None
    if vision_model.startswith("ollama:"):
        return vision_model
    return f"ollama:{vision_model}"


def load_mode(path: Path | None = None) -> dict[str, Any]:
    target = path or OCR_MODE_PATH
    if not target.is_file():
        return {
            "backend": "tesseract",
            "llm_enabled": False,
            "vision_model": "ollama:moondream",
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "backend": "tesseract",
            "llm_enabled": False,
            "vision_model": "ollama:moondream",
        }
    backend = data.get("backend") or "tesseract"
    if backend not in {"tesseract", "ollama"}:
        backend = "tesseract"
    vision = data.get("vision_model") or "ollama:moondream"
    llm = bool(data.get("llm_enabled")) if "llm_enabled" in data else backend == "ollama"
    return {"backend": backend, "llm_enabled": llm, "vision_model": vision}


def apply_mode(
    current: dict[str, Any],
    mode: str,
    vision_model: str | None = None,
) -> dict[str, Any]:
    backend = "ollama" if mode == "ollama" else "tesseract"
    updated = dict(current)
    updated["backend"] = backend
    updated["llm_enabled"] = backend == "ollama"
    if mode == "ollama":
        model = _normalize_vision_model(vision_model)
        if model:
            updated["vision_model"] = model
        else:
            updated.setdefault("vision_model", "ollama:moondream")
    return updated


def llm_enabled(data: dict[str, Any] | str) -> bool:
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return False
        return bool(parsed.get("llm_enabled")) or parsed.get("backend") == "ollama"
    return bool(data.get("llm_enabled")) or data.get("backend") == "ollama"


def write_mode(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or OCR_MODE_PATH
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"tesseract", "ollama", "is-llm"}:
        raise SystemExit(
            f"Usage: {sys.argv[0]} tesseract|ollama [VISION_MODEL] | is-llm"
        )

    command = sys.argv[1]
    current = load_mode()
    if command == "is-llm":
        raise SystemExit(0 if llm_enabled(current) else 1)

    vision_model = sys.argv[2] if len(sys.argv) > 2 else None
    updated = apply_mode(current, command, vision_model)
    write_mode(updated)
    print(f"OCR mode set to {command} in {OCR_MODE_PATH}")


if __name__ == "__main__":
    main()
