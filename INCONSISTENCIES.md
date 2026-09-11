# 2markdown — Known Inconsistencies

Findings from a code audit (2026-09-06), grouped by priority. Each verified by reading the code. Fix opportunistically; delete entries as they land.

## Must fix

(none currently tracked)

## Should fix

- **Config tests depend on `.ocr-mode` being absent** — `tests/test_config.py::TestSettingsIgnoreEnv` asserts `ocr_backend == "tesseract"` and `ollama_vision_model == "ollama:moondream"`, but `config.py` folds `.ocr-mode` into those defaults at import. After `make build ollama` both tests fail on a clean tree. They should patch the module constants instead of asserting the shipped default.

## Worth a look

(none currently tracked)

## Checked and clean

- **PDF OCR threshold** — `should_fallback(..., pdf_path=)` and `extract_pages` inspect per-page fitz text, not total markdown length (`twomarkdown/converter/pdf_ocr.py`).
- **OCR flag coherence** — `resolve_ocr_backend` treats `--ocr-backend=ollama` as LLM-on (`twomarkdown/cli.py`).
- **Manifest skip** — retries `failed`, different `ocr_backend`, and checksum mismatch (`twomarkdown/batch/manifest.py`).
- **Batch parallelism** — `conversion_config.parallel_workers` / `--workers` uses a thread pool (`twomarkdown/batch/processor.py`).
- **Installable package is `twomarkdown`** — imports are `twomarkdown.*`; the CLI entry is `twomarkdown = "twomarkdown.cli:app"`.
- **LibreOffice in the image** — `.doc`/`.ppt` (and related) convert via `soffice` in `docker/backend.Dockerfile`.
- **iWork** — bundles convert `preview.pdf` only; IWA/Kreuzberg/AppleScript removed.
