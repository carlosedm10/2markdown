"""Thread-safe span collector for one batch run."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Process-wide so worker threads (file timeout pool) see the same run.
_batch_lock = threading.Lock()
_batch: _BatchCollector | None = None
_tls = threading.local()


@dataclass
class SpanRecord:
    name: str
    duration_ms: float
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list[SpanRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
        }
        if self.attrs:
            payload["attrs"] = self.attrs
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload

    def walk(self) -> Iterator[SpanRecord]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class FileTrace:
    source: str
    converter: str | None = None
    spans: list[SpanRecord] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "converter": self.converter,
            "spans": [span.to_dict() for span in self.spans],
            "notes": self.notes,
        }

    def span_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for root in self.spans:
            for span in root.walk():
                totals[span.name] = totals.get(span.name, 0.0) + span.duration_ms
        return totals

    def ocr_confidences(self) -> list[float]:
        values: list[float] = []
        for note in self.notes:
            raw = note.get("confidence")
            if isinstance(raw, (int, float)):
                values.append(float(raw))
        return values

    def tool_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for note in self.notes:
            kind = str(note.get("kind") or "")
            if not kind:
                continue
            counts[kind] = counts.get(kind, 0) + 1
        if self.converter:
            key = f"converter.{self.converter}"
            counts[key] = counts.get(key, 0) + 1
        return counts


def _file_state() -> dict[str, Any] | None:
    return getattr(_tls, "file", None)


class _BatchCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files: list[FileTrace] = []
        self.batch_spans: list[SpanRecord] = []

    def add_file(self, trace: FileTrace) -> None:
        with self._lock:
            self.files.append(trace)

    def add_batch_span(self, span: SpanRecord) -> None:
        with self._lock:
            self.batch_spans.append(span)


def current_batch() -> _BatchCollector | None:
    with _batch_lock:
        return _batch


def begin_batch() -> _BatchCollector:
    global _batch
    collector = _BatchCollector()
    with _batch_lock:
        _batch = collector
    _tls.file = None
    return collector


def end_batch() -> _BatchCollector | None:
    global _batch
    with _batch_lock:
        collector = _batch
        _batch = None
    return collector


def begin_file(source: Path) -> None:
    _tls.file = {
        "source": str(source.resolve()),
        "converter": None,
        "stack": [],
        "roots": [],
        "notes": [],
    }


def set_converter(name: str) -> None:
    state = _file_state()
    if state is None:
        return
    state["converter"] = name


def note(kind: str, **attrs: Any) -> None:
    state = _file_state()
    if state is None:
        return
    state["notes"].append({"kind": kind, **attrs})


def end_file() -> FileTrace:
    state = _file_state() or {
        "source": "",
        "converter": None,
        "roots": [],
        "notes": [],
    }
    trace = FileTrace(
        source=str(state.get("source") or ""),
        converter=state.get("converter"),
        spans=list(state.get("roots") or []),
        notes=list(state.get("notes") or []),
    )
    _tls.file = None
    collector = current_batch()
    if collector is not None:
        collector.add_file(trace)
    return trace


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Record a named interval. No-op when no batch is active."""
    collector = current_batch()
    state = _file_state()
    if collector is None and not state:
        yield
        return
    record = SpanRecord(name=name, duration_ms=0.0, attrs=dict(attrs))
    stack: list[SpanRecord]
    if state:
        stack = state["stack"]
        stack.append(record)
    else:
        stack = []
    started = time.perf_counter()
    try:
        yield
    finally:
        record.duration_ms = (time.perf_counter() - started) * 1000.0
        if state:
            stack.pop()
            if stack:
                stack[-1].children.append(record)
            else:
                state["roots"].append(record)
        elif collector is not None:
            collector.add_batch_span(record)
