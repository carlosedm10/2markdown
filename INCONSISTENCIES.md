# 2markdown — Known Inconsistencies

Findings from a code audit (2026-09-06), grouped by priority. Each verified by reading the code. Fix opportunistically; delete entries as they land.

## Must fix

(none currently tracked)

## Should fix

1. **Installable package is named `src`** — `pyproject.toml` `packages = ["src"]`, so every import is `src.*` while the project/script is `twomarkdown` / `2markdown`. Confusing on PYTHONPATH and for packaging.
2. **Kreuzberg iWork extra has no Makefile target** — `IWORK_BACKEND=kreuzberg` needs `uv sync --extra iwork-kreuzberg`, but package changes must go through `make uv-*`. There is no `make` extra-sync target, so the documented path fights the Makefile skill.
3. **Config is process-global mutable singletons** — `conversion_config` / `llm_config` are mutated by the CLI and by `process_batch` (OCR flag restored in a `finally`). Tests and concurrent use share one object.

## Worth a look

4. **`IWORK_USE_APP_EXPORT` is a no-op in Docker** — `convert_bundle` logs once and ignores it; AppleScript never runs in the Linux image.
5. **No LICENSE** — public repo.
6. **LibreOffice is not in the image** — `.doc`/`.ppt` only convert if `soffice` is already on PATH.

## Checked and clean

- **PDF OCR threshold** — `should_fallback(..., pdf_path=)` and `extract_pages` inspect per-page fitz text, not total markdown length (`src/converter/pdf_ocr.py`).
- **OCR flag coherence** — `resolve_ocr_backend` treats `--ocr-backend=ollama` as LLM-on (`src/cli.py`).
- **Manifest skip** — retries `failed`, different `ocr_backend`, and checksum mismatch (`src/batch/manifest.py`).
- **Batch parallelism** — `PARALLEL_WORKERS` / `--workers` uses a thread pool (`src/batch/processor.py`).
