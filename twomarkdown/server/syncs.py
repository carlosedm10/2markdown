"""Watched-folder syncs: an `input` -> `output` folder pair with a preset,
watched by its own background thread while `enabled`.

Persisted the same way as `presets.py` — one JSON file under the platform's
per-user app-support directory. The *watcher thread* is not persisted the same
way: it is recreated from the file at `start_all()` (called once at server
startup) and does not survive the process exiting, so a change dropped into a
watched folder while the desktop app itself is closed is only picked up once
it starts again — not queued and replayed.

Kept deliberately simple, per this task's own instruction, and the limits are
part of that simplicity, not oversights:

* **One conversion in flight per sync.** A `watchfiles` batch that lands while
  the previous run for the same sync is still going is dropped, not queued —
  the next batch (or the next `watchfiles` poll, since new/changed files
  during that window are still on disk) picks up whatever is left.
* **No cross-sync dedup.** Two syncs watching overlapping folders each start
  their own job; nothing here notices the overlap.
* **No debounce beyond `watchfiles`' own.** A burst of saves in the same
  folder is already batched by `watchfiles` into one change-set per callback;
  this module does not add a second debounce on top.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from twomarkdown.server.presets import PRESETS_DIR
from twomarkdown.server.schemas import Sync, SyncCreateRequest, SyncUpdateRequest

logger = logging.getLogger(__name__)

SYNCS_FILE = PRESETS_DIR / "syncs.json"

JobRunner = Callable[[Sync], None]
_job_runner: JobRunner | None = None


def set_job_runner(runner: JobRunner) -> None:
    """Wired once by `server/app.py` at startup.

    Keeps this module from importing `server/jobs.py` (which pulls in the
    whole batch engine) just to run one job — `app.py` already sits above
    both and is the natural place to connect them.
    """
    global _job_runner
    _job_runner = runner


class _Watcher:
    """One background thread per enabled sync, driven by `watchfiles.watch`."""

    def __init__(self, sync: Sync) -> None:
        self.sync = sync
        self._stop = threading.Event()
        self._run_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop, name=f"sync-{sync.id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the watcher thread to actually exit.

        `watchfiles.watch(..., stop_event=self._stop)` polls `self._stop` and
        returns once it is set, but nothing previously waited for that to
        happen — `stop()` alone leaves the thread (and the native watcher it
        drives) running in the background indefinitely. A daemon thread still
        blocked in `watchfiles.watch` when the interpreter starts tearing
        down segfaults instead of exiting quietly (see INCONSISTENCIES.md);
        `stop_all()`/`_stop_watcher()` below always pair `stop()` with this.
        """
        self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        try:
            import watchfiles
        except ImportError:
            logger.warning("watchfiles not installed; sync %s stays idle", self.sync.id)
            return
        path = Path(self.sync.input)
        if not path.exists():
            logger.warning("Sync %s: input %s does not exist", self.sync.id, path)
            return
        try:
            for _changes in watchfiles.watch(path, stop_event=self._stop):
                self._run_once()
        except Exception as exc:
            logger.warning("Sync %s watcher stopped: %s", self.sync.id, exc)

    def _run_once(self) -> None:
        if _job_runner is None:
            logger.debug("Sync %s: no job runner registered yet", self.sync.id)
            return
        if not self._run_lock.acquire(blocking=False):
            return  # a run is already in flight; the next batch picks up the rest
        try:
            _job_runner(self.sync)
            record_run(self.sync.id)
        except Exception as exc:
            logger.warning("Sync %s job failed: %s", self.sync.id, exc)
        finally:
            self._run_lock.release()


_lock = threading.Lock()
_watchers: dict[str, _Watcher] = {}


def _load() -> dict[str, dict]:
    if not SYNCS_FILE.is_file():
        return {}
    try:
        data = json.loads(SYNCS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not read %s: %s", SYNCS_FILE, exc)
        return {}


def _save(data: dict[str, dict]) -> None:
    SYNCS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNCS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_syncs() -> list[Sync]:
    with _lock:
        data = _load()
    return [Sync.model_validate(v) for v in data.values()]


def get_sync(sync_id: str) -> Sync | None:
    with _lock:
        raw = _load().get(sync_id)
    return Sync.model_validate(raw) if raw else None


def create_sync(req: SyncCreateRequest) -> Sync:
    sync = Sync(
        id=str(uuid.uuid4()),
        input=req.input,
        output=req.output,
        preset_id=req.preset_id,
        enabled=req.enabled,
        last_run=None,
        files_today=0,
    )
    with _lock:
        data = _load()
        data[sync.id] = sync.model_dump(mode="json")
        _save(data)
    if sync.enabled:
        _start_watcher(sync)
    return sync


def update_sync(sync_id: str, req: SyncUpdateRequest) -> Sync | None:
    """Flip fields on an existing sync in place, preserving `id`/`last_run`/
    `files_today` (see docs/desktop-app.md's `PATCH /api/syncs/{id}` note,
    M8) — the delete+recreate dance this replaces lost that identity, and a
    watcher restart/stop only happens when `enabled` actually changed."""
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    with _lock:
        data = _load()
        raw = data.get(sync_id)
        if raw is None:
            return None
        was_enabled = bool(raw.get("enabled", True))
        raw.update(updates)
        data[sync_id] = raw
        _save(data)
        sync = Sync.model_validate(raw)
    if sync.enabled and not was_enabled:
        _start_watcher(sync)
    elif not sync.enabled and was_enabled:
        _stop_watcher(sync_id)
    return sync


def delete_sync(sync_id: str) -> bool:
    _stop_watcher(sync_id)
    with _lock:
        data = _load()
        if sync_id not in data:
            return False
        del data[sync_id]
        _save(data)
    return True


def record_run(sync_id: str) -> None:
    with _lock:
        data = _load()
        raw = data.get(sync_id)
        if raw is None:
            return
        raw["last_run"] = datetime.now(UTC).isoformat()
        raw["files_today"] = int(raw.get("files_today") or 0) + 1
        _save(data)


def _start_watcher(sync: Sync) -> None:
    with _lock:
        if sync.id in _watchers:
            return
        watcher = _Watcher(sync)
        _watchers[sync.id] = watcher
    watcher.start()


def _stop_watcher(sync_id: str) -> None:
    with _lock:
        watcher = _watchers.pop(sync_id, None)
    if watcher is not None:
        watcher.stop()
        watcher.join(timeout=5.0)


def start_all() -> None:
    """Re-arm watchers for every persisted, enabled sync.

    Called once at server startup — the watcher threads themselves are not
    persisted, only the sync records are, so this is what "resumes" them.
    """
    for sync in list_syncs():
        if sync.enabled:
            _start_watcher(sync)


def stop_all() -> None:
    """Stop and join every running watcher thread.

    Called from the FastAPI lifespan's shutdown phase (`server/app.py`) so
    `make app` exits cleanly, and from the test suite's per-test teardown so
    no `watchfiles.watch` thread a test started ever survives past that test
    — see `_Watcher.join`'s docstring for why an unstopped one is dangerous,
    not just untidy.
    """
    with _lock:
        watchers = list(_watchers.values())
        _watchers.clear()
    for watcher in watchers:
        watcher.stop()
    for watcher in watchers:
        watcher.join(timeout=5.0)
