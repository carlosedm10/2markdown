"""Project-local telemetry dumps for bottleneck research (not the export folder)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def resolve_project_dir() -> Path | None:
    raw = os.environ.get("TWOMARKDOWN_TELEMETRY_DIR")
    if raw:
        return Path(raw)
    from twomarkdown.config import conversion_config

    return conversion_config.project_telemetry_dir


def write_run(summary: dict[str, Any], traces: dict[str, Any]) -> Path | None:
    root = resolve_project_dir()
    if root is None:
        return None
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"run-{stamp}.json"
    payload = {"summary": summary, "traces": traces}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest = root / "latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    index = root / "index.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "path": path.name,
                    "generated_at": summary.get("generated_at"),
                    "wall_ms": summary.get("wall_ms"),
                    "this_run": summary.get("this_run"),
                    "stages": (summary.get("stages") or [])[:8],
                    "config": summary.get("config"),
                }
            )
            + "\n"
        )
    return path
