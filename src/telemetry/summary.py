"""Aggregate manifest + traces into a JSON-serializable export summary."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.batch.manifest import FileRecord, Manifest
from src.telemetry.collector import FileTrace, SpanRecord


def reliability_for_file(
    record: FileRecord,
    trace: FileTrace | None,
) -> float | None:
    """0–1 score. Skipped files are omitted (None)."""
    if record.status == "skipped":
        return None
    if record.status == "failed":
        return 0.0
    chars = record.char_count or 0
    if chars <= 0:
        return 0.15
    confs = trace.ocr_confidences() if trace is not None else []
    if not confs:
        return 1.0
    mean = sum(confs) / len(confs)
    # Native conversion succeeded; OCR quality scales the remaining 60%.
    return round(0.4 + 0.6 * max(0.0, min(mean, 100.0)) / 100.0, 3)


def _flatten_span_ms(spans: list[SpanRecord]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for root in spans:
        for span in root.walk():
            totals[span.name] = totals.get(span.name, 0.0) + span.duration_ms
    return totals


def build_summary(
    *,
    manifest: Manifest,
    traces: list[FileTrace],
    batch_spans: list[SpanRecord],
    wall_ms: float,
    input_dir: Path,
    output_dir: Path,
    converted: int,
    failed: int,
    skipped: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    by_source = {trace.source: trace for trace in traces}
    files: list[dict[str, Any]] = []
    tool_counts: Counter[str] = Counter()
    span_ms: dict[str, float] = _flatten_span_ms(batch_spans)
    reliabilities: list[float] = []

    for record in manifest.records.values():
        trace = by_source.get(record.source)
        score = reliability_for_file(record, trace)
        if score is not None:
            reliabilities.append(score)
        file_spans = trace.span_totals() if trace else {}
        for name, ms in file_spans.items():
            span_ms[name] = span_ms.get(name, 0.0) + ms
        if trace is not None:
            tool_counts.update(trace.tool_counts())
        converter = trace.converter if trace is not None else None
        files.append(
            {
                "source": record.source,
                "status": record.status,
                "error": record.error,
                "output": record.output,
                "ocr_backend": record.ocr_backend,
                "duration_ms": record.duration_ms,
                "char_count": record.char_count,
                "converter": converter,
                "reliability": score,
                "span_ms": {k: round(v, 2) for k, v in file_spans.items()},
            }
        )

    files.sort(key=lambda row: (row["status"] != "failed", -(row["duration_ms"] or 0)))
    attempted = converted + failed
    success_rate = (converted / attempted) if attempted else 1.0
    mean_reliability = (
        sum(reliabilities) / len(reliabilities) if reliabilities else None
    )
    durations = [
        row["duration_ms"]
        for row in files
        if row["status"] != "skipped" and row["duration_ms"] is not None
    ]
    tool_total = sum(tool_counts.values()) or 1
    tools = [
        {
            "name": name,
            "count": count,
            "pct": round(100.0 * count / tool_total, 1),
        }
        for name, count in tool_counts.most_common()
    ]
    stages = [
        {"name": name, "duration_ms": round(ms, 2)}
        for name, ms in sorted(span_ms.items(), key=lambda item: item[1], reverse=True)
        if ms > 0
    ]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "wall_ms": round(wall_ms, 2),
        "this_run": {
            "converted": converted,
            "failed": failed,
            "skipped": skipped,
            "success_rate": round(success_rate, 4),
        },
        "reliability": {
            "mean": (
                round(mean_reliability, 3) if mean_reliability is not None else None
            ),
            "n": len(reliabilities),
            "how": (
                "Failed files score 0. Empty output scores 0.15. "
                "Native text without OCR scores 1. "
                "OCR files mix 40% success + 60% mean Tesseract confidence."
            ),
        },
        "timing": {
            "file_count_timed": len(durations),
            "sum_file_ms": round(sum(durations), 2) if durations else 0,
            "mean_file_ms": round(sum(durations) / len(durations), 2)
            if durations
            else None,
            "max_file_ms": round(max(durations), 2) if durations else None,
        },
        "tools": tools,
        "stages": stages,
        "config": config,
        "files": files,
        "manifest_updated_at": None,
    }


def config_snapshot() -> dict[str, Any]:
    from src.config import conversion_config, llm_config, pdf_ocr_config

    return {
        "ocr_enabled": conversion_config.ocr_enabled,
        "ocr_backend": conversion_config.ocr_backend,
        "ocr_hybrid": conversion_config.ocr_hybrid,
        "parallel_workers": conversion_config.parallel_workers,
        "llm_enabled": llm_config.llm_enabled,
        "pdf_ocr_enabled": pdf_ocr_config.pdf_ocr_enabled,
        "extract_tables": conversion_config.extract_tables,
        "clean_markdown": conversion_config.clean_markdown,
    }


def traces_payload(
    traces: list[FileTrace],
    batch_spans: list[SpanRecord],
) -> dict[str, Any]:
    return {
        "batch_spans": [span.to_dict() for span in batch_spans],
        "files": [trace.to_dict() for trace in traces],
    }


def as_plain(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj
