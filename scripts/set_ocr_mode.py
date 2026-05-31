#!/usr/bin/env python3
"""Update .env OCR mode (tesseract or ollama). Stdlib only — runs on the host before Docker."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

TESSERACT = {
    "OCR_BACKEND": "tesseract",
    "LLM_ENABLED": "false",
}

OLLAMA = {
    "OCR_BACKEND": "ollama",
    "LLM_ENABLED": "true",
    "OLLAMA_BASE_URL": "http://ollama:11434/v1",
    "OLLAMA_VISION_MODEL": "ollama:llama3.2-vision:11b",
}

MODES = {"tesseract": TESSERACT, "ollama": OLLAMA}


def _load_lines() -> list[str]:
    if not ENV_PATH.exists():
        template = ROOT / "env_template"
        if not template.exists():
            raise SystemExit("env_template not found; run from the project root.")
        return template.read_text(encoding="utf-8").splitlines()
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _apply(lines: list[str], updates: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    return out


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        raise SystemExit(f"Usage: {sys.argv[0]} tesseract|ollama")

    mode = sys.argv[1]
    lines = _apply(_load_lines(), MODES[mode])
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OCR mode set to {mode} in {ENV_PATH}")


if __name__ == "__main__":
    main()
