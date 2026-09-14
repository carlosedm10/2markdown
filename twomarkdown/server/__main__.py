"""`python -m twomarkdown.server` — run the desktop app's local API server.

Development entry point; the packaged app spawns this (or an embedded
sidecar) instead. Binds to loopback only — this server is never meant to be
reachable from another machine.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "twomarkdown.server.app:app",
        host="127.0.0.1",
        port=8765,
        log_level="info",
        # N10: without a bound, uvicorn's graceful shutdown waits
        # indefinitely for every open connection (an events WebSocket,
        # chiefly) to close on its own before it forcibly closes them —
        # which is exactly what let the process (and its `uv run` parent)
        # survive SIGTERM forever, port closed but the python process still
        # in state S. `server/app.py`'s lifespan shutdown now actively
        # closes those sockets itself, so this window is a backstop, not the
        # only thing standing between SIGTERM and a clean exit.
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
