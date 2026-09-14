"""2markdown — batch folder-to-markdown conversion."""

import os

# Must be set before any `import fitz` / `import pymupdf`.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

# Preempt tqdm's global write lock from ever creating a real
# `multiprocessing.RLock()` (a POSIX named semaphore). `tqdm.std.
# TqdmDefaultWriteLock.create_mp_lock()` — invoked by every progress bar's
# `__init__`, including the ones in `batch/processor.py` and
# `converter/pdf_ocr.py` — creates that lock lazily on first use "for
# multiprocessing safety", but every progress bar in this codebase only ever
# runs on threads within one process (`ThreadPoolExecutor`, never a fork'd or
# spawned child), so the lock protects nothing here. Left alone, that unused
# semaphore is what `multiprocessing.resource_tracker` reports as "leaked
# semaphore objects to clean up at shutdown" when the server process exits —
# a real semaphore our own code caused tqdm to create, not a third-party
# library's own multiprocessing use. `create_mp_lock` only creates one when
# the class does not already have an `mp_lock` attribute (`hasattr` check),
# so setting it to `None` here — before any tqdm bar exists, since this
# module is imported first by everything under `twomarkdown.*` — makes every
# later `create_mp_lock()` call a no-op instead.
try:
    from tqdm.std import TqdmDefaultWriteLock as _TqdmDefaultWriteLock

    _TqdmDefaultWriteLock.mp_lock = None
except ImportError:  # pragma: no cover - tqdm is a hard dependency
    pass
