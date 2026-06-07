# 2markdown

Batch-convert files or folders to Markdown using [MarkItDown](https://github.com/microsoft/markitdown). Runs entirely in Docker.

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop on macOS/Windows)

## Setup

```bash
make fresh-setup
make build              # Tesseract OCR — fast, offline, no extra downloads
# or
make build ollama       # Ollama vision OCR — higher quality on images & scanned PDFs
```

`make fresh-setup` writes `.env` from the template and stops any existing containers.

`make build` builds the converter image and locks in your OCR mode:

| Command | OCR engine | Extra |
|---------|------------|-------|
| `make build` | **Tesseract** (default) | Nothing else to install |
| `make build ollama` | **Ollama** (`moondream`) | Starts Ollama in Docker and pulls the default vision model (~2 GB RAM) |

> Make does not support `--flags`. Use `make build ollama` (two words), not `make build --ollama`.

To switch OCR mode later, run the other `make build` variant again.

## Convert

```bash
make process INPUT="/Users/you/Documents/reports"
make process INPUT="/Users/you/Documents/report.pdf"
```

### What happens

1. Your input path is mounted read/write into the container (same absolute path).
2. Every supported file is converted to `.md`.
3. Output is written **beside the input** as a sibling folder:

| Input | Output |
|-------|--------|
| `/docs/reports/` (folder) | `/docs/reports_2markdown/` — mirrors the folder tree |
| `/docs/report.pdf` (file) | `/docs/report_2markdown/report.md` |

4. A manifest at `<output>/.2markdown-manifest.json` records `ok`, `failed`, and `skipped` per file.
5. Files that already have a newer `.md` are skipped (use `.env` `SKIP_EXISTING=false` to force re-convert).

### Examples

```bash
# Folder of mixed Office docs, PDFs, images
make process INPUT="$HOME/Downloads/client-docs"

# Single scanned PDF (best with Ollama)
make build ollama
make process INPUT="$HOME/Desktop/scan.pdf"
```

## Supported formats

MarkItDown `[all]`: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xls`, `.html`, `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.epub`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.zip`, `.msg`, `.wav`, `.mp3`.

**Apple iWork** (directory bundles or zip archives on disk):

| Format | Default backend | Notes |
|--------|-----------------|-------|
| `.numbers` | [numbers-parser](https://pypi.org/project/numbers-parser/) | Tables → markdown |
| `.key` | [keynote-parser](https://pypi.org/project/keynote-parser/) | Slide text from IWA archives |
| `.pages` | `preview.pdf` when present, else IWA text | Weaker than export; see below |

Embedded files inside a bundle (e.g. `MyDoc.pages/Data/*.png`) are **not** converted separately — only the bundle root is processed.

Optional **Kreuzberg** backend for stronger Pages coverage (Elastic-2.0 license):

```bash
# In Docker image build, add the extra before make build
uv sync --extra iwork-kreuzberg
```

Set `IWORK_BACKEND=kreuzberg` in `.env`.

Legacy `.doc`/`.ppt` and video files soft-fail with a warning; the batch continues.

### iWork limitations

- Floating text boxes in Pages may be missing without `preview.pdf` or Kreuzberg
- Videos inside `Data/` are not transcribed
- Very new Keynote versions may need an updated `keynote-parser`

## Scanned PDFs

When MarkItDown extracts fewer than 50 characters from a PDF page, each page is rasterized and OCR'd (Tesseract or Ollama, depending on your build). Output includes `## Page N — OCR` sections.

**Ollama vision models:** The default `moondream` fits machines with ~8 GB RAM. For higher quality on scans (if you have ~11 GB+ free), run `make build ollama OLLAMA_MODEL=llama3.2-vision:11b` and set `OLLAMA_VISION_MODEL=ollama:llama3.2-vision:11b` in `.env`.

## Configuration

`.env` is managed by `make fresh-setup` and `make build`. Advanced tuning:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKIP_EXISTING` | `true` | Skip if output `.md` is newer than source |
| `PDF_OCR_MIN_CHARS` | `50` | Threshold for scanned-PDF fallback |
| `PDF_OCR_DPI` | `200` | Rasterization quality for page OCR |
| `OLLAMA_VISION_MODEL` | `ollama:moondream` | Vision model (set by `make build ollama`; override e.g. `ollama:llava` if you have more RAM) |
| `IWORK_ENABLED` | `true` | Convert `.pages` / `.key` / `.numbers` bundles |
| `IWORK_BACKEND` | `native` | `native` or `kreuzberg` (optional extra) |

## Makefile reference

| Target | Description |
|--------|-------------|
| `fresh-setup` | Create/reset `.env`, stop containers |
| `build` | Build image + enable Tesseract OCR |
| `build ollama` | Build image + start Ollama + pull vision model |
| `process INPUT=...` | Convert a file or folder |
| `stop` | Stop all containers |
| `show-ollama-logs` | Stream Ollama logs |
| `tests` | Run unit tests in Docker |

## Development

Maintainer targets (`lint`, `format`, `tests`, `uv-add`, …) all run inside Docker. See the `Makefile`.

## Notes

- Remote image URLs are not fetched unless `FETCH_REMOTE_IMAGES=true` in `.env`
- Audio transcription may call external services via MarkItDown extras
- Only local paths are processed — no URL or YouTube ingestion
