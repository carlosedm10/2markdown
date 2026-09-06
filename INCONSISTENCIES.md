# 2markdown — Known Inconsistencies

Findings from a code audit (2026-09-06), grouped by priority. Each verified by reading the code. Fix opportunistically; delete entries as they land.

## Must fix

(none currently tracked)

## Should fix

1. **Installable package is named `src`** — `pyproject.toml` `packages = ["src"]`, so every import is `src.*` while the project/script is `twomarkdown` / `2markdown`. Confusing on PYTHONPATH and for packaging.
2. **Kreuzberg iWork extra has no Makefile target** — `IWORK_BACKEND=kreuzberg` needs `uv sync --extra iwork-kreuzberg`, but package changes must go through `make uv-*`. There is no `make` extra-sync target, so the documented path fights the Makefile skill.
3. **Config is process-global mutable singletons** — `conversion_config` / `llm_config` are mutated by the CLI and by `process_batch` (OCR flag restored in a `finally`). Tests and concurrent use share one object.

## Worth a look

4. **Batch is strictly sequential** — Ollama-per-page on large PDFs will be slow; no worker pool.
5. **`IWORK_USE_APP_EXPORT` is Darwin-only** — AppleScript export never runs inside the Linux converter image; dead in the documented Docker flow.
6. **No LICENSE** — public repo.

## Checked and clean

- **PDF OCR threshold** — `should_fallback(..., pdf_path=)` and `extract_pages` inspect per-page fitz text, not total markdown length (`src/converter/pdf_ocr.py`).
- **OCR flag coherence** — `resolve_ocr_backend` treats `--ocr-backend=ollama` as LLM-on (`src/cli.py`).
- **Manifest skip** — retries `failed` and different `ocr_backend` (`src/batch/manifest.py`).
