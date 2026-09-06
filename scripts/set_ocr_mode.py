#!/usr/bin/env python3
"""Update OCR mode in src/config.py. Stdlib only — runs on the host before Docker."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "src" / "config.py"

_BACKEND_RE = re.compile(
    r'^OCR_BACKEND: Literal\["tesseract", "ollama"\] = "(tesseract|ollama)"\s*$',
    re.MULTILINE,
)
_LLM_RE = re.compile(r"^LLM_ENABLED = (True|False)\s*$", re.MULTILINE)
_VISION_RE = re.compile(r'^OLLAMA_VISION_MODEL = "([^"]+)"\s*$', re.MULTILINE)


def apply_mode(text: str, mode: str, vision_model: str | None = None) -> str:
    backend = "ollama" if mode == "ollama" else "tesseract"
    llm = "True" if mode == "ollama" else "False"
    text, n_backend = _BACKEND_RE.subn(
        f'OCR_BACKEND: Literal["tesseract", "ollama"] = "{backend}"',
        text,
        count=1,
    )
    text, n_llm = _LLM_RE.subn(f"LLM_ENABLED = {llm}", text, count=1)
    if n_backend != 1 or n_llm != 1:
        raise SystemExit(
            f"Could not find OCR_BACKEND / LLM_ENABLED assignments in {CONFIG_PATH}"
        )
    if mode == "ollama" and vision_model:
        model = vision_model if vision_model.startswith("ollama:") else f"ollama:{vision_model}"
        text, n_vision = _VISION_RE.subn(
            f'OLLAMA_VISION_MODEL = "{model}"',
            text,
            count=1,
        )
        if n_vision != 1:
            raise SystemExit(f"Could not find OLLAMA_VISION_MODEL in {CONFIG_PATH}")
    return text


def llm_enabled(text: str) -> bool:
    match = _LLM_RE.search(text)
    if not match:
        raise SystemExit(f"Could not find LLM_ENABLED in {CONFIG_PATH}")
    return match.group(1) == "True"


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"tesseract", "ollama", "is-llm"}:
        raise SystemExit(f"Usage: {sys.argv[0]} tesseract|ollama [VISION_MODEL] | is-llm")

    text = CONFIG_PATH.read_text(encoding="utf-8")
    command = sys.argv[1]
    if command == "is-llm":
        raise SystemExit(0 if llm_enabled(text) else 1)

    vision_model = sys.argv[2] if len(sys.argv) > 2 else None
    CONFIG_PATH.write_text(apply_mode(text, command, vision_model), encoding="utf-8")
    print(f"OCR mode set to {command} in {CONFIG_PATH}")


if __name__ == "__main__":
    main()
