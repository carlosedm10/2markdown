"""Compare conversion methods by replaying the same inputs."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twomarkdown.batch.processor import BatchResult, process_batch
from twomarkdown.config import conversion_config


@dataclass
class Method:
    name: str
    workers: int = 1
    ocr_enabled: bool = False
    skip_existing: bool = False


def run_methods(
    input_dir: Path,
    output_root: Path,
    methods: list[Method],
    *,
    convert: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Run each method against the same input tree; return wall-clock rows."""
    from unittest.mock import patch

    rows: list[dict[str, Any]] = []
    previous_workers = conversion_config.parallel_workers
    try:
        for method in methods:
            out = output_root / method.name
            out.mkdir(parents=True, exist_ok=True)
            conversion_config.parallel_workers = method.workers
            started = time.perf_counter()
            if convert is not None:
                with patch(
                    "twomarkdown.converter.markitdown_converter.convert_file",
                    side_effect=convert,
                ):
                    result: BatchResult = process_batch(
                        input_dir,
                        out,
                        skip_existing=method.skip_existing,
                        ocr_enabled=method.ocr_enabled,
                        show_progress=False,
                    )
            else:
                result = process_batch(
                    input_dir,
                    out,
                    skip_existing=method.skip_existing,
                    ocr_enabled=method.ocr_enabled,
                    show_progress=False,
                )
            wall_ms = (time.perf_counter() - started) * 1000.0
            rows.append(
                {
                    "method": method.name,
                    "workers": method.workers,
                    "ocr_enabled": method.ocr_enabled,
                    "wall_ms": round(wall_ms, 2),
                    "converted": result.converted,
                    "failed": result.failed,
                    "skipped": result.skipped,
                }
            )
    finally:
        conversion_config.parallel_workers = previous_workers
    return rows
