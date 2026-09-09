"""2markdown — batch folder-to-markdown conversion."""

import os

# Must be set before any `import fitz` / `import pymupdf`.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
