"""Local FastAPI server that fronts the twomarkdown engine for the desktop app.

The actual app and routes live in `twomarkdown.server.app` — this re-export
only exists so `uvicorn twomarkdown.server:app` (and `python -m
twomarkdown.server`) keep working without callers needing to know the routes
moved out of `__init__.py` and into their own modules (`app.py`, `jobs.py`,
`system.py`, `presets.py`, `syncs.py`) once there was real wiring to put in
them. See `docs/desktop-app.md` for the endpoint list and event schema.
"""

from twomarkdown.server.app import app

__all__ = ["app"]
