# 2markdown

Batch-convert files or folders to Markdown using [MarkItDown](https://github.com/microsoft/markitdown). The converter runs in Docker; optional vision OCR uses [Ollama](https://ollama.com) on your host for better GPU/Metal performance.

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on macOS/Windows)
- [Ollama](https://ollama.com) on the host — only when using vision OCR (`make build ollama`)

## Setup

```bash
make fresh-setup
make build              # Tesseract OCR — fast, offline, no extra downloads
# or
make build ollama       # Ollama vision OCR — higher quality on images & scanned PDFs
make up                 # optional: keep backend container + host Ollama running
```

`make fresh-setup` writes a secrets-only `.env` from the template and tears the stack down (`make down`). Tuning lives in `twomarkdown/config.py`. OCR engine selection is stored in gitignored `.ocr-mode` (`make build` / `make build ollama`).

`make build` builds the converter image and locks in your OCR mode:

| Command | OCR engine | Extra |
|---------|------------|-------|
| `make build` | **Tesseract** (default) | Nothing else to install |
| `make build ollama` | **Ollama** (`moondream`) | Installs/pulls the vision model on host Ollama (~2 GB RAM) |

> Make does not support `--flags`. Use `make build ollama` (two words), not `make build --ollama`.

To switch OCR mode later, run the other `make build` variant again.

The converter reaches host Ollama at `http://host.docker.internal:11434/v1` (default in `twomarkdown/config.py`).

**OCR flags:** `--ocr-backend=ollama` enables the vision LLM (no silent Tesseract fallback). `--ollama` is a shortcut for the same. Default Tesseract language is `eng+spa` (`tesseract_lang` in `twomarkdown/config.py`).

## Convert

```bash
make process INPUT="/Users/you/Documents/reports"
make process INPUT="/Users/you/Documents/report.pdf"
make process INPUT="/Users/you/Documents/reports" VERBOSE=1
make process INPUT="/Users/you/Documents/reports" DRY_RUN=1
make process INPUT="/Users/you/Documents/reports" WORKERS=4
make process INPUT="/Users/you/Documents/reports" FORCE=1
make process INPUT="/Users/you/Documents/report.pdf" OUTPUT="/tmp/out"
make process INPUT="/Users/you/Documents/reports" OCR_BACKEND=ollama
```

When OCR mode is Ollama (`make build ollama` writes `.ocr-mode`), `make process` ensures host Ollama is running before converting.

### What happens

1. Only the input path and the sibling `*_2markdown` output directory are mounted (same absolute paths).
2. Every supported file is converted to `.md`.
3. Output is written **beside the input** as a sibling folder:

| Input | Output |
|-------|--------|
| `/docs/reports/` (folder) | `/docs/reports_2markdown/` — mirrors the folder tree |
| `/docs/report.pdf` (file) | `/docs/report_2markdown/report.md` |

4. A manifest at `<output>/.2markdown-manifest.json` records `ok`, `failed`, and `skipped` per file (including OCR backend). Open `<output>/2markdown-report.html` (or the PDF) for success rate, reliability, tools used, and timings.
5. Skip logic respects OCR backend: failed files are retried; switching Tesseract → Ollama re-converts. Pass `--force` to the CLI, or set `skip_existing = False` in `twomarkdown/config.py`, to re-convert everything.
6. `.md` source files are not reconverted unless `convert_existing_md = True` in `twomarkdown/config.py`.
7. `make process` also writes span dumps under `telemetry/` in this repo (gitignored) so you can compare bottlenecks across runs. `--no-report` skips the HTML/PDF.

### Examples

```bash
# Folder of mixed Office docs, PDFs, images
make process INPUT="$HOME/Downloads/client-docs"

# Single scanned PDF (best with Ollama)
make build ollama
make process INPUT="$HOME/Desktop/scan.pdf"
```

## Supported formats

MarkItDown `[all]`: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xls`, `.html`, `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.tif`, `.tiff`, `.zip`, `.msg`, `.wav`, `.mp3`.

**E-readers** (native parsers, not MarkItDown):

| Format | Notes |
|--------|-------|
| `.epub` | Spine order, title/author metadata |
| `.fb2` | FictionBook sections and paragraphs |
| `.mobi`, `.azw`, `.azw3` | Unpacked HTML → markdown chapters |
| `.eml` | Native RFC 822 (From/To/Subject + body) |
| `.xlsx`, `.xlsm` | Native sheets as `## Sheet:` + GFM tables |
| `.doc`, `.ppt`, `.xls`, `.odt`, `.ods`, `.odp`, `.rtf` | LibreOffice (`soffice`) in the image |

Plain `.zip` archives are unpacked into the output tree and converted (Office/iWork zips are left intact). File type can be sniffed from magic bytes (`sniff_filetype` in `twomarkdown/config.py`).

**Apple iWork** (directory bundles or zip archives on disk). Conversion uses the bundled **`preview.pdf`** and the same PDF pipeline as a normal PDF. No IWA parser, Kreuzberg, or Pages.app.

| Format | Notes |
|--------|-------|
| `.pages`, `.key`, `.numbers` | Requires `preview.pdf` inside the bundle. Missing preview → that file fails, the batch continues. |

Numbers is the rendered preview, not per-sheet GFM tables. To keep layout, export PDF from the Apple app first if the bundle has no preview.

Raster extras (opt-in, not in the default image): `make uv-sync EXTRA=heic` for `.heic`/`.heif`, `make uv-sync EXTRA=svg` for `.svg`. Without the extra, those files soft-fail with that Make command in the error.

Local audio: `make uv-sync EXTRA=audio-whisper` transcribes `.wav`/`.mp3` with CPU Whisper before MarkItDown. Without the extra, audio still goes to MarkItDown (or fails that file, not the batch).

Video files still soft-fail.

## Scanned PDFs

OCR is **per page**. For each page, PyMuPDF extracts native text; if a page has fewer than `pdf_ocr_min_chars` characters, that page is rasterized and OCR'd (Tesseract or Ollama, depending on your build). Mixed PDFs (digital text + scans) OCR only the weak pages. Output uses `## Page N` with an `### OCR` subsection for scanned pages (prose, not fenced code). Tables are extracted when possible (`### Table (page N)`).

If Tesseract confidence is below `ocr_confidence_min` and Ollama is enabled, 2markdown retries that image with the vision model (`ocr_hybrid`).

**Ollama vision models:** The default `moondream` fits machines with ~8 GB RAM. For higher quality on scans (if you have ~11 GB+ free), run `make build ollama OLLAMA_MODEL=llama3.2-vision:11b` (that also updates `.ocr-mode`).

## Configuration

Feature flags and tuning live in [`twomarkdown/config.py`](twomarkdown/config.py). `.env` is secrets only (`make fresh-setup` copies `env_template`). `make build` / `make build ollama` write gitignored `.ocr-mode` instead of editing `config.py`.

| Setting | Default | Purpose |
|---------|---------|---------|
| `skip_existing` | `true` | Skip if output `.md` is newer than source |
| `convert_existing_md` | `false` | Reconvert `.md` sources in the input tree |
| `tesseract_lang` | `eng+spa` | Tesseract language(s) for OCR |
| `clean_markdown` | `true` | Fix mojibake, hyphenation, repeated headers |
| `extract_tables` | `true` | PDF/Excel tables as GitHub-flavored markdown |
| `ocr_hybrid` | `true` | Fall back to Ollama when Tesseract confidence is low |
| `ocr_confidence_min` | `60` | Minimum Tesseract mean confidence (0–100) |
| `describe_figures` | `true` | Caption images with little OCR text (needs Ollama) |
| `extract_assets` | `true` | Dump PDF embeds next to the `.md` |
| `emit_chunks` | `false` | Write `.chunks.json` sidecar (heading-aware) |
| `parallel_workers` | `4` | Parallel file conversions |
| `file_timeout_sec` | `300` | Per-file timeout |
| `explode_zip` | `true` | Unpack generic zips before converting |
| `sniff_filetype` | `true` | Prefer magic bytes over a lying extension |
| `write_export_report` | `true` | Write `2markdown-report.html` / `.pdf` in the output folder |
| `pdf_ocr_min_chars` | `50` | Per-page threshold for scanned-PDF fallback |
| `pdf_ocr_dpi` | `200` | Rasterization quality for page OCR |
| `ollama_base_url` | `http://host.docker.internal:11434/v1` | Host Ollama API (Docker → host) |
| `ollama_vision_model` | `ollama:moondream` | Vision model (`make build ollama OLLAMA_MODEL=…`) |
| `iwork_enabled` | `true` | Convert `.pages` / `.key` / `.numbers` via `preview.pdf` |

Credentials belong in `.env` and are loaded by `Secrets` in `twomarkdown/config.py` (empty until a feature needs a key).

## Makefile reference

| Target | Description |
|--------|-------------|
| `fresh-setup` | Create/reset secrets `.env`, `make down` |
| `build` | Build image + enable Tesseract OCR |
| `build ollama` | Build image + ensure host Ollama + pull vision model |
| `up` | Start backend container (+ host Ollama if LLM enabled) |
| `down` | `docker compose down --remove-orphans` |
| `restart` | Restart the backend container |
| `process INPUT=...` | Convert (`VERBOSE=1`, `DRY_RUN=1`, `WORKERS=n`, `FORCE=1`, `OCR_BACKEND=`, `OUTPUT=`, `NO_OCR=1`, `EMIT_CHUNKS=1`) |

Make variables forwarded into the Typer CLI:

| Make | CLI |
|------|-----|
| `VERBOSE=1` | `-v` |
| `DRY_RUN=1` | `--dry-run` |
| `WORKERS=n` | `--workers n` |
| `FORCE=1` | `--force` |
| `OCR_BACKEND=tesseract\|ollama` | `--ocr-backend …` |
| `OUTPUT=/path` | `--output` (also bind-mounted) |
| `NO_OCR=1` | `--no-ocr` |
| `EMIT_CHUNKS=1` | `--emit-chunks` |
| `stop-ollama` | Stop host Ollama |
| `test` | Unit tests (`TEST=` for one path) |
| `bench` | Compare conversion methods (serial vs parallel) |

## Development

Maintainer targets all run **inside Docker**. Do not run `uv add` / `uv lock` on the host.

| Target | Description |
|--------|-------------|
| `make uv-add PKG="pkg>=1.0"` | Add a dependency and refresh `uv.lock` |
| `make uv-remove PKG=pkg` | Remove a dependency |
| `make uv-lock` | Refresh `uv.lock` |
| `make uv-lock-regenerate` | Regenerate the lock file from scratch |
| `make uv-sync EXTRA=heic` | Install an optional extra (`heic`, `svg`, `audio-whisper`) plus `dev` |
| `make uv-update` / `make uv-update PKG=foo` | Upgrade all packages, or one |
| `make lint` / `make lint-fix` / `make format` | Ruff check, auto-fix, format |
| `make test` / `make test TEST=path` | Unit tests |
| `make test-integration` | Integration tests |
| `make bench` | Replay the same files with different methods |
| `make logs` / `make backend-shell` | Backend logs and shell |

GitHub Actions calls `make lint` and `make test` (`CI=true` → native `uv`). Optional `make test-integration` is non-blocking.

## Notes

- Remote image URLs are not fetched unless `fetch_remote_images` is true in `twomarkdown/config.py` (or `--fetch-remote-images`)
- Audio transcription may call external services via MarkItDown unless extra `audio-whisper` is installed
- Only local paths are processed — no URL or YouTube ingestion
