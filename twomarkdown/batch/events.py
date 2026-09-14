"""Thread-safe event sink between the engine and the desktop server.

`process_batch()`, `converter/pdf_ocr.py` and the OCR/review passes in
`agents/image_ocr.py` call the small `emit*` helpers here at the natural
points (file/page start and finish, live semaphore state). With no sink
attached every call is a cheap no-op — `twomarkdown.cli` and the existing test
suite see identical behaviour to before this module existed. A job's live view
is a consumer bolted on the side, not a new code path the engine depends on.

Two different things live here, both process-wide like
`telemetry/collector.py`'s `_batch` (a local server runs one job at a time):

* **The sink** (`set_sink`/`emit`) — where finished events go. `twomarkdown.
  server.jobs` attaches one per job and buffers what it receives.
* **Per-thread context** (`stage`, `begin_file`) — which file/page/stage the
  *calling* thread is working on right now, read back by `TrackedSemaphore` so
  a `semaphores` event can name real holders instead of a bare count.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EventSink = Callable[[dict[str, Any]], None]

_lock = threading.Lock()
_sink: EventSink | None = None
_tls = threading.local()


def set_sink(sink: EventSink | None) -> None:
    """Attach (or, with None, detach) the sink for the job now running.

    Also resets the running USD total (see `add_cost`) — a local server runs
    one job at a time (this module's own docstring), so a fresh sink always
    means a fresh job whose cost starts back at zero.
    """
    global _sink
    with _lock:
        _sink = sink
    with _cost_lock:
        global _usd_so_far, _unpriced_call_seen
        _usd_so_far = 0.0
        _unpriced_call_seen = False


def has_sink() -> bool:
    with _lock:
        return _sink is not None


def emit(event: dict[str, Any]) -> None:
    """Send one event to the attached sink. No-op with none attached (CLI)."""
    with _lock:
        sink = _sink
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        # A misbehaving consumer must not cost the batch a file.
        pass


# ---------------------------------------------------------------------------
# Per-thread context: which file/page/stage this worker is on right now.
# ---------------------------------------------------------------------------


def _ctx() -> dict[str, Any]:
    ctx = getattr(_tls, "ctx", None)
    if ctx is None:
        ctx = {"file": None, "page": None, "stage": None}
        _tls.ctx = ctx
    return ctx


def current_holder() -> dict[str, Any]:
    """This thread's `{file, page, stage}`, for a semaphore holder entry."""
    ctx = _ctx()
    return {
        "file": ctx["file"] or "",
        "page": ctx["page"],
        "stage": ctx["stage"] or "ocr",
    }


_UNSET = object()


@contextmanager
def stage(name: str, *, page: int | Any = _UNSET):
    """Mark "this thread is doing `name`" for the semaphore holder list.

    `page` defaults to leaving whatever page an outer `stage()` already set —
    `agents/image_ocr.py`'s low-level OCR/figure calls nest inside
    `converter/pdf_ocr.py`'s per-page loop, which is the one that knows the
    page number; a bare `describe_image_bytes_llm()` call outside that loop
    (e.g. a standalone image) just carries no page, which is correct too.

    Independent of `page_started`/`page_done` below: those are the OCR-page WS
    events the mockup's Convertir screen shows; this is only ever read back by
    `TrackedSemaphore` to report who holds a permit right now.
    """
    ctx = _ctx()
    prev = (ctx["stage"], ctx["page"])
    ctx["stage"] = name
    if page is not _UNSET:
        ctx["page"] = page
    try:
        yield
    finally:
        ctx["stage"], ctx["page"] = prev


# ---------------------------------------------------------------------------
# File / page events
# ---------------------------------------------------------------------------


def begin_file(index: int, path: Path, *, pages: int = 0) -> None:
    ctx = _ctx()
    ctx["file"] = str(path)
    _bump_active(1)
    emit({"type": "file_started", "index": index, "path": str(path), "pages": pages})


def end_file(
    index: int, status: str, seconds: float, *, reason: str | None = None
) -> None:
    """Report a file finished. `index` is explicit — a timed-out file is
    abandoned from the *scheduling* thread, not the worker that began it, so
    this cannot rely on the calling thread's own context the way `begin_file`
    sets it."""
    _bump_active(-1)
    emit(
        {
            "type": "file_done",
            "index": index,
            "status": status,
            "seconds": seconds,
            "reason": reason,
        }
    )
    ctx = _ctx()
    if ctx["file"] is not None:
        ctx["file"], ctx["page"], ctx["stage"] = None, None, None


def page_started(index: int, page: int) -> None:
    emit({"type": "page_started", "index": index, "page": page})


def page_done(
    index: int,
    page: int,
    *,
    engine: str,
    seconds: float,
    fallback: bool = False,
    review_changes: list[dict[str, Any]] | None = None,
) -> None:
    emit(
        {
            "type": "page_done",
            "index": index,
            "page": page,
            "engine": engine,
            "seconds": seconds,
            "fallback": fallback,
            "review_changes": review_changes or [],
        }
    )


def cost(usd_so_far: float) -> None:
    emit({"type": "cost", "usd_so_far": usd_so_far})


_cost_lock = threading.Lock()
_usd_so_far = 0.0
# Set once per job the first time `add_cost(None, unknown=True)` fires — i.e.
# some cloud call's price genuinely could not be determined. Read back by
# `usd_unknown()` so a job that made cloud calls but priced every one of them
# (total possibly $0, e.g. a free tier) can be told apart from a job where the
# total is unknowable (see N16): `_usd_so_far` alone conflates the two, since
# both leave it at `0.0`.
_unpriced_call_seen = False


def add_cost(usd: float | None, *, unknown: bool = False) -> None:
    """Add one priced cloud call to the job's running USD total and report it.

    `usd` is `None` when the call's price could not be determined (an
    unrecognised model with no manual price on file) — nothing to add to the
    total. Pass `unknown=True` in that case (callers get it from the same
    pricing lookup that produced the `None`, e.g. `estimate._cloud_price`'s
    second return value) so the job-level total can still say "unknown" later
    rather than reading as a plain zero (see N16). Call sites:
    `agents/image_ocr.py` (OCR/figure calls) and `agents/page_review.py` (the
    proofreader pass), right after a cloud call returns its usage.
    """
    if unknown:
        global _unpriced_call_seen
        with _cost_lock:
            _unpriced_call_seen = True
    if not usd:
        return
    global _usd_so_far
    with _cost_lock:
        _usd_so_far += usd
        total = _usd_so_far
    emit({"type": "cost", "usd_so_far": total})


def usd_unknown() -> bool:
    """Whether this job has hit at least one cloud call whose price could not
    be determined — the caller (`server/jobs.py`, at `job_done`) uses this to
    decide between reporting `usd: 0.0` (every call was priced, total is
    genuinely zero) and `usd: null` (unknown, per `docs/desktop-app.md`)."""
    with _cost_lock:
        return _unpriced_call_seen


def log(level: str, message: str, *, ts: str | None = None) -> None:
    """`ts` (ISO 8601): when the line was actually produced, not received.

    Passed in by `server/jobs.py`'s `_EngineLogHandler` from the log record's
    own `created` time when this reaches it through a logger; defaults to
    "now" for a direct call. Without a server-assigned `ts`, a WebSocket
    reconnect (which replays the whole buffered backlog at once) re-stamps
    every line with the reconnect time instead of when it happened (see N2).
    """
    emit(
        {
            "type": "log",
            "level": level,
            "message": message,
            "ts": ts or datetime.now(UTC).isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# CPU lane — no semaphore backs this (it's the ThreadPoolExecutor fan-out in
# batch/processor.py), so it is just two counters instead of a TrackedSemaphore.
# ---------------------------------------------------------------------------

_cpu_lock = threading.Lock()
_cpu_workers = 1
_cpu_active = 0


def set_cpu_workers(workers: int) -> None:
    global _cpu_workers
    with _cpu_lock:
        _cpu_workers = max(1, workers)


def _bump_active(delta: int) -> None:
    global _cpu_active
    with _cpu_lock:
        _cpu_active = max(0, _cpu_active + delta)


def _cpu_state() -> dict[str, int]:
    with _cpu_lock:
        return {"workers": _cpu_workers, "active": _cpu_active}


# ---------------------------------------------------------------------------
# GPU / cloud lanes — real semaphores, wrapped so every acquire/release also
# reports the combined `semaphores` event.
# ---------------------------------------------------------------------------

_lanes: dict[str, TrackedSemaphore] = {}
_lanes_lock = threading.Lock()

# Lanes the `semaphores` event actually reports. Anything registered under a
# different name (agents/image_ocr.py's dead `_figure_lock`, kept wrapped only
# because it is still acquired somewhere in principle) is tracked the same way
# but never surfaced, so it cannot double-count GPU holders.
GPU_LANE = "gpu"
CLOUD_LANE = "cloud"


class TrackedSemaphore:
    """A `threading.Semaphore` that reports its own state through the sink.

    Wraps rather than subclasses `Semaphore` (not reliably subclassable across
    CPython versions) and reports on every acquire/release rather than
    polling, so the desktop app's live view never lags behind the real lock.
    """

    def __init__(self, value: int, *, lane: str) -> None:
        self._sem = threading.Semaphore(value)
        self.lane = lane
        self.permits = value
        self._state_lock = threading.Lock()
        self.holders: list[dict[str, Any]] = []
        self.waiting = 0
        with _lanes_lock:
            _lanes[lane] = self

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        with self._state_lock:
            self.waiting += 1
        _emit_semaphores()
        acquired = self._sem.acquire(*args, **kwargs)
        with self._state_lock:
            self.waiting = max(0, self.waiting - 1)
            if acquired:
                holder = current_holder()
                holder["_thread_id"] = threading.get_ident()
                self.holders.append(holder)
        _emit_semaphores()
        return acquired

    def release(self) -> None:
        with self._state_lock:
            tid = threading.get_ident()
            for i, holder in enumerate(self.holders):
                if holder.get("_thread_id") == tid:
                    del self.holders[i]
                    break
            else:
                # Fallback: releasing thread pushed no matching holder (should
                # not happen in practice) — drop the oldest rather than crash.
                if self.holders:
                    self.holders.pop(0)
        self._sem.release()
        _emit_semaphores()

    def __enter__(self) -> TrackedSemaphore:
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "permits": self.permits,
                "held_by": [
                    {k: v for k, v in holder.items() if k != "_thread_id"}
                    for holder in self.holders
                ],
                "waiting": self.waiting,
            }


def _lane_snapshot(name: str) -> dict[str, Any] | None:
    with _lanes_lock:
        lane = _lanes.get(name)
    return lane.snapshot() if lane is not None else None


def _emit_semaphores() -> None:
    if not has_sink():
        return
    gpu = _lane_snapshot(GPU_LANE) or {"permits": 0, "held_by": [], "waiting": 0}
    cloud_raw = _lane_snapshot(CLOUD_LANE) or {"permits": 0, "held_by": []}
    emit(
        {
            "type": "semaphores",
            "gpu": {
                "permits": gpu["permits"],
                "held_by": gpu["held_by"],
                "waiting": gpu["waiting"],
            },
            "cloud": {
                "permits": cloud_raw["permits"],
                "in_use": len(cloud_raw["held_by"]),
            },
            "cpu": _cpu_state(),
        }
    )
