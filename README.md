# 2markdown

Batch-convert a folder of documents to Markdown using [MarkItDown](https://github.com/microsoft/markitdown), with local OCR (Tesseract) and optional Ollama vision for images and scanned PDFs.

## Features

- Recursively walks an input directory and writes `.md` files that **mirror the folder tree**
- Converts Office, PDF, HTML, images, archives, and more via `markitdown[all]`
- **Tesseract OCR** (default, no API tokens) for markdown image refs and scanned PDF fallback
- **Optional Ollama** (`llama3.2-vision:11b`) for higher-quality image/PDF page OCR
- Per-file **soft-fail**: warnings + manifest; batch never aborts on a single bad file

## Quick start

### Local (fastest)

```bash
make setup          # .env + uv deps + sample files in data/in
make dev            # convert data/in -> data/in_2markdown (verbose)
ls -la data/in_2markdown/
```

### Docker

```bash
cp env_template .env   # if you have not run make setup
make build
make docker-seed       # optional: sample files under data/in
make convert INPUT=/data/my-folder
```

Custom folders (output defaults to a sibling `<folder>_2markdown`):

```bash
# ~/Downloads/folder_to_process -> ~/Downloads/folder_to_process_2markdown
uv run python -m src.cli --input ~/Downloads/folder_to_process

make convert-local INPUT=~/Downloads/folder_to_process

# override output location
make convert-local INPUT=~/Downloads/folder_to_process OUTPUT=~/Desktop/out
```

## CLI

```bash
uv run python -m src.cli \
  --input /path/to/docs \
  --ocr \
  --skip-existing \
  --verbose
```

Output is written to `/path/to/docs_2markdown` (same parent as input) unless you pass `--output`.

| Flag | Default | Description |
|------|---------|-------------|
| `--output` / `-o` | `<input>_2markdown` | Override output directory |
| `--ocr` / `--no-ocr` | on | OCR markdown `![...](...)` image refs |
| `--skip-existing` / `--force` | skip | Skip if output `.md` is newer than source |
| `--ocr-backend` | `tesseract` | `tesseract` or `ollama` |
| `--llm-enabled` | off | Use Ollama vision for OCR |
| `--pdf-ocr` / `--no-pdf-ocr` | on | Page OCR when PDF text is thin |
| `--fetch-remote-images` | off | Fetch HTTP images for OCR |

Exit code `1` if any file failed (batch still completes).

## Supported formats

Handled by MarkItDown `[all]`: `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.xls`, `.html`, `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.epub`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.zip`, `.msg`, `.wav`, `.mp3`.

Legacy `.doc`/`.ppt` and video files are not supported and soft-fail with a warning.

## Scanned PDFs

If MarkItDown returns fewer than `PDF_OCR_MIN_CHARS` characters (default 50), each page is rasterized with PyMuPDF and OCR’d (Tesseract or Ollama). Output includes `## Page N — OCR` sections.

## Optional Ollama OCR

```bash
ollama pull llama3.2-vision:11b
```

```bash
uv run python -m src.cli \
  --input ./data/in --output ./data/out \
  --ocr-backend ollama --llm-enabled
```

Or use the Docker Ollama profile:

```bash
docker compose --profile llm up -d ollama
```

Set `OLLAMA_BASE_URL` in `.env` (e.g. `http://ollama:11434/v1` inside compose).

## Configuration

Environment variables (see `env_template`):

- `OCR_ENABLED`, `OCR_BACKEND`, `FETCH_REMOTE_IMAGES`
- `PDF_OCR_ENABLED`, `PDF_OCR_MIN_CHARS`, `PDF_OCR_DPI`, `PDF_OCR_MAX_PAGES`
- `LLM_ENABLED`, `OLLAMA_VISION_MODEL`

## Manifest

`<input>_2markdown/.2markdown-manifest.json` records per-file `ok`, `failed`, or `skipped` for resume and debugging.

## Development

Tests follow **pytest class-based** conventions (aligned with our Django `APITestCase` style):

- One `Test<Feature>` class per module
- Descriptive docstrings per scenario (`"""process_batch() — ..."""`)
- Section banners (`# --- Filtering ---`)
- Shared Arrange fixtures in `tests/conftest.py`
- Explicit `expected_stats` / `expected_record` dicts where stable

```bash
make tests          # unit tests
make test TEST=tests/test_integration_convert.py  # integration (needs markitdown)
make lint
make format
```

## Local-only notes

- Remote image URLs are not fetched unless `--fetch-remote-images`
- Audio transcription may use external services via MarkItDown’s `speechrecognition` extra
- YouTube URLs are not processed (folder walk uses local files only)
