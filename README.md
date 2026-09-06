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

`make fresh-setup` writes `.env` from the template and tears the stack down (`make down`).

`make build` builds the converter image and locks in your OCR mode:

| Command | OCR engine | Extra |
|---------|------------|-------|
| `make build` | **Tesseract** (default) | Nothing else to install |
| `make build ollama` | **Ollama** (`moondream`) | Installs/pulls the vision model on host Ollama (~2 GB RAM) |

> Make does not support `--flags`. Use `make build ollama` (two words), not `make build --ollama`.

To switch OCR mode later, run the other `make build` variant again.

The converter reaches host Ollama at `http://host.docker.internal:11434/v1` (set automatically in `.env`).

**OCR flags:** `--ocr-backend=ollama` enables the vision LLM (no silent Tesseract fallback). `--ollama` is a shortcut for the same. Default Tesseract language is `eng+spa` (`TESSERACT_LANG`).

## Convert

```bash
make process INPUT="/Users/you/Documents/reports"
make process INPUT="/Users/you/Documents/report.pdf"
make process INPUT="/Users/you/Documents/reports" VERBOSE=1   # verbose logs
```

When `LLM_ENABLED=true`, `make process` ensures host Ollama is running before converting.

### What happens

1. Only the input path and the sibling `*_2markdown` output directory are mounted (same absolute paths).
2. Every supported file is converted to `.md`.
3. Output is written **beside the input** as a sibling folder:

| Input | Output |
|-------|--------|
| `/docs/reports/` (folder) | `/docs/reports_2markdown/` — mirrors the folder tree |
| `/docs/report.pdf` (file) | `/docs/report_2markdown/report.md` |

4. A manifest at `<output>/.2markdown-manifest.json` records `ok`, `failed`, and `skipped` per file (including OCR backend).
5. Skip logic respects OCR backend: failed files are retried; switching Tesseract → Ollama re-converts. Use `SKIP_EXISTING=false` to force re-convert everything.
6. `.md` source files are not reconverted unless `CONVERT_EXISTING_MD=true`.

### Examples

```bash
# Folder of mixed Office docs, PDFs, images
make process INPUT="$HOME/Downloads/client-docs"

# Single scanned PDF (best with Ollama)
make build ollama
make process INPUT="$HOME/Desktop/scan.pdf"
```

## Supported formats

MarkItDown `[all]`: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xls`, `.html`, `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.zip`, `.msg`, `.wav`, `.mp3`.

**E-readers** (native parsers, not MarkItDown):

| Format | Notes |
|--------|-------|
| `.epub` | Spine order, title/author metadata |
| `.fb2` | FictionBook sections and paragraphs |
| `.mobi`, `.azw`, `.azw3` | Unpacked HTML → markdown chapters |
| `.eml` | Native RFC 822 (From/To/Subject + body) |
| `.xlsx` | Native sheets as `## Sheet:` + GFM tables |
| `.doc`, `.ppt` | LibreOffice/`soffice` if installed on the converter image |

Plain `.zip` archives are unpacked into the output tree and converted (Office/iWork zips are left intact). File type can be sniffed from magic bytes (`SNIFF_FILETYPE=true`).

**Apple iWork** (directory bundles or zip archives on disk):

| Format | Default backend | Notes |
|--------|-----------------|-------|
| `.numbers` | [numbers-parser](https://pypi.org/project/numbers-parser/) | Tables → markdown |
| `.key` | [keynote-parser](https://pypi.org/project/keynote-parser/) | Slide text from IWA archives |
| `.pages` | `preview.pdf` when present, else IWA text | Weaker than export; see below |

Embedded files inside a bundle (e.g. `MyDoc.pages/Data/*.png`) are **not** converted separately — only the bundle root is processed.

Optional **Kreuzberg** backend for stronger Pages coverage (Elastic-2.0 license):

Kreuzberg is an optional extra (`iwork-kreuzberg`) and is **not** in the default image. There is no `make` target for extras yet (see `INCONSISTENCIES.md`).

Set `IWORK_BACKEND=kreuzberg` in `.env`.

Legacy `.doc`/`.ppt` and video files soft-fail with a warning; the batch continues.

### iWork limitations

- Floating text boxes in Pages may be missing without `preview.pdf` or Kreuzberg
- Videos inside `Data/` are not transcribed
- Very new Keynote versions may need an updated `keynote-parser`

## Scanned PDFs

OCR is **per page**. For each page, PyMuPDF extracts native text; if a page has fewer than `PDF_OCR_MIN_CHARS` characters, that page is rasterized and OCR'd (Tesseract or Ollama, depending on your build). Mixed PDFs (digital text + scans) OCR only the weak pages. Output uses `## Page N` with an `### OCR` subsection for scanned pages (prose, not fenced code). Tables are extracted when possible (`### Table (page N)`).

If Tesseract confidence is below `OCR_CONFIDENCE_MIN` and Ollama is enabled, 2markdown retries that image with the vision model (`OCR_HYBRID=true`).

**Ollama vision models:** The default `moondream` fits machines with ~8 GB RAM. For higher quality on scans (if you have ~11 GB+ free), run `make build ollama OLLAMA_MODEL=llama3.2-vision:11b` and set `OLLAMA_VISION_MODEL=ollama:llama3.2-vision:11b` in `.env`.

## Configuration

`.env` is managed by `make fresh-setup` and `make build`. Advanced tuning:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKIP_EXISTING` | `true` | Skip if output `.md` is newer than source |
| `CONVERT_EXISTING_MD` | `false` | Reconvert `.md` sources in the input tree |
| `TESSERACT_LANG` | `eng+spa` | Tesseract language(s) for OCR |
| `CLEAN_MARKDOWN` | `true` | Fix mojibake, hyphenation, repeated headers |
| `EXTRACT_TABLES` | `true` | PDF/Excel tables as GitHub-flavored markdown |
| `OCR_HYBRID` | `true` | Fall back to Ollama when Tesseract confidence is low |
| `OCR_CONFIDENCE_MIN` | `60` | Minimum Tesseract mean confidence (0–100) |
| `DESCRIBE_FIGURES` | `true` | Caption images with little OCR text (needs Ollama) |
| `EXTRACT_ASSETS` | `true` | Dump PDF embeds next to the `.md` |
| `EMIT_CHUNKS` | `false` | Write `.chunks.json` sidecar (heading-aware) |
| `PARALLEL_WORKERS` | `1` | Parallel file conversions |
| `FILE_TIMEOUT_SEC` | `300` | Per-file timeout |
| `EXPLODE_ZIP` | `true` | Unpack generic zips before converting |
| `SNIFF_FILETYPE` | `true` | Prefer magic bytes over a lying extension |
| `PDF_OCR_MIN_CHARS` | `50` | Per-page threshold for scanned-PDF fallback |
| `PDF_OCR_DPI` | `200` | Rasterization quality for page OCR |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | Host Ollama API (Docker → host) |
| `OLLAMA_VISION_MODEL` | `ollama:moondream` | Vision model (set by `make build ollama`; override e.g. `ollama:llava` if you have more RAM) |
| `IWORK_ENABLED` | `true` | Convert `.pages` / `.key` / `.numbers` bundles |
| `IWORK_BACKEND` | `native` | `native` or `kreuzberg` (optional extra) |

## Makefile reference

| Target | Description |
|--------|-------------|
| `fresh-setup` | Create/reset `.env`, `make down` |
| `build` | Build image + enable Tesseract OCR |
| `build ollama` | Build image + ensure host Ollama + pull vision model |
| `up` | Start backend container (+ host Ollama if LLM enabled) |
| `down` | `docker compose down --remove-orphans` |
| `restart` | Restart the backend container |
| `process INPUT=...` | Convert a file or folder (mounts input + output only; use `VERBOSE=1` for `-v`) |
| `stop-ollama` | Stop host Ollama |
| `test` | Unit tests (`TEST=` for one path) |

## Development

Maintainer targets all run **inside Docker**. Do not run `uv add` / `uv lock` on the host.

| Target | Description |
|--------|-------------|
| `make uv-add PKG="pkg>=1.0"` | Add a dependency and refresh `uv.lock` |
| `make uv-remove PKG=pkg` | Remove a dependency |
| `make uv-lock` | Refresh `uv.lock` |
| `make uv-lock-regenerate` | Regenerate the lock file from scratch |
| `make uv-update` / `make uv-update PKG=foo` | Upgrade all packages, or one |
| `make lint` / `make lint-fix` / `make format` | Ruff check, auto-fix, format |
| `make test` / `make test TEST=path` | Unit tests |
| `make test-integration` | Integration tests |
| `make logs` / `make backend-shell` | Backend logs and shell |

GitHub Actions calls `make lint` and `make test` (`CI=true` → native `uv`). Optional `make test-integration` is non-blocking.

## Notes

- Remote image URLs are not fetched unless `FETCH_REMOTE_IMAGES=true` in `.env`
- Audio transcription may call external services via MarkItDown extras
- Only local paths are processed — no URL or YouTube ingestion
