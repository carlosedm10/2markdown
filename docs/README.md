# 2markdown — The One Doc

Local batch converter: a file or folder in, Markdown plus a JSON manifest out. The converter process runs in Docker. Optional vision OCR talks to **Ollama on the host** so GPU/Metal stay outside the container.

## The taxonomy

These names repeat in config, CLI flags, the manifest, and frontmatter. They *are* the mental model.

| Term | Covers |
|---|---|
| **batch** | Walk input, convert each file, soft-fail, write sibling `*_2markdown/` |
| **OCR backend** | `tesseract` (default, `eng+spa`) or `ollama` (host vision model); hybrid uses Tesseract confidence then vision |
| **PDF page OCR** | Per-page native-text threshold; only weak pages are rasterized; tables and page headings stay in reading order |
| **iWork bundle** | `.pages` / `.key` / `.numbers` as one unit; `Data/` images may be OCR'd into that markdown |
| **e-reader** | `.epub` / `.fb2` / `.mobi` / `.azw` / `.azw3` via `src/converter/ereader.py` |
| **manifest** | `<output>/.2markdown-manifest.json` — status, `ocr_backend`, checksum, timing |
| **export report** | `<output>/2markdown-report.html` and `.pdf` — success, reliability, tools, timings |
| **trace / telemetry** | `.2markdown-trace.json` in the export folder; `telemetry/run-*.json` in the repo (`make process` sets `TWOMARKDOWN_TELEMETRY_DIR`) |

## How it's built

```
CLI (src.cli) → paths → processor → walker (optional zip explode, magic-byte suffix)
                              ↓
         iWork | e-reader | eml | xlsx | legacy Office | MarkItDown
                              ↓
         PDF compose (pages, OCR prose, tables, assets) | image OCR / figures
                              ↓
         clean markdown → frontmatter + .md + manifest + HTML/PDF report [+ optional .chunks.json]
```

### The principles that matter

- **One file, one chance to fail.** The batch continues; the manifest stores the error.
- **Skip is semantic, not just mtime.** Failed files retry; changing OCR engine reconverts.
- **Host GPU, container CPU.** Tesseract lives in the image; Ollama stays on the host (`host.docker.internal`).
- **Make is the contract.** Package, lint, and test recipes exist once; CI flips transport with `CI=true`.

## How data flows

- **Reads:** a host path (file or tree). `make process` bind-mounts only that path and the sibling output directory.
- **Writes:** mirrored `.md` files (YAML frontmatter includes `source`, `ocr_backend`, `title`, `ocr_pages`, `tables`, `language`, `char_count`), the manifest (checksum + duration), `2markdown-report.html` / `.pdf` (batch stats and per-file reliability), `.2markdown-trace.json` (named spans), optional `_assets/` and `.chunks.json`. OCR text is cached under the output dir. Generic zips unpack into `.unzipped/` then markdown is written as if the zip were a folder (`archive.zip/a.pdf` → `archive/a.md`). `make process` also appends a run dump under `telemetry/` in the repo for bottleneck research.
- **OCR:** Tesseract first when hybrid is on; low confidence or empty text can call Ollama. Tiny images are skipped. Remote images are fetched only if enabled, capped at 8 MiB.

### Entities

- **FileRecord:** one source path, status, optional error, output path, OCR backend, checksum, duration, char count.
- **BatchResult:** converted / failed / skipped counts, failed paths, and `planned` paths for `--dry-run`.
- **Reliability:** 0 on failure, 1 when native text converted, scaled by Tesseract confidence when OCR ran.

### One example, end to end

1. `make process INPUT=/docs/scan.pdf` (after `make fresh-setup` and `make build` or `make build ollama`).
2. CLI normalizes to batch root + default output `/docs/scan_2markdown/`.
3. MarkItDown converts the PDF; PyMuPDF measures each page; short pages are rasterized and OCR'd; tables become GFM under `### Table (page N)`.
4. Markdown is cleaned and written with richer frontmatter; the manifest records `ok` with `ocr_backend` and checksum; `2markdown-report.html` / `.pdf` summarize the batch.

## Key decisions and caveats (why it is this way)

- **Sibling output folder, not in-place overwrite** — avoids clobbering sources and makes skip-by-mtime possible.
- **Zips become folders in the output tree** — members are staged under `.unzipped/` (not under the input path), then mirrored without that prefix so a zip is not a conversion unit. Only `*.zip` is exploded; EPUB and Office/iWork zip containers stay intact. Sniffing retries iCloud `EDEADLK` locks instead of aborting the batch.
- **Numbers conversion is isolated on protobuf clashes** — `numbers-parser` and `keynote-parser` both register `TSDArchives.proto`; a second `.numbers` file in the same process retries in a spawned interpreter.
- **Pages without `preview.pdf` still try the preview JPEG** — many zip `.pages` files only ship `preview.jpg`; IWA deserialize often fails on those, so OCR of the full preview (not the tiny `preview-web.jpg`) is the fallback.
- **Magic bytes beat a lying extension** — a PNG named `Foto.pdf` is OCR'd as an image, not sent through the PDF pipeline.
- **LibreOffice ships in the image** — `.doc`/`.ppt` convert via headless Writer/Impress; the image is larger because of that.
- **`--ocr-backend=ollama` implies LLM** — a second `--llm-enabled` gate caused silent Tesseract fallback.
- **PDF OCR is per page** — a mixed PDF with some extractable text used to skip scans entirely. OCR is prose under `## Page N`, not fenced code.
- **Native e-readers, not only MarkItDown** — EPUB spine order and FB2/MOBI needed their own module. EPUBs are copied onto local disk before parsing because Docker bind-mounts of iCloud Drive often break `zipfile` seek (`Bad Zip file`).
- **Ollama is not stopped by `make down`** — tearing down Docker must not kill a host daemon other tools use (`make stop-ollama` is explicit).
- **Tesseract then vision** — hybrid OCR spends GPU only when Tesseract confidence is low.
- **Make is still the process CLI** — `VERBOSE`, `DRY_RUN`, and `WORKERS` are Make vars forwarded into `src.cli`; converter knobs live in `src/config.py`.
- **Settings in code, secrets in `.env`** — flags and tuning are Pydantic `BaseModel` defaults; `Secrets` is the only `BaseSettings` class and reads `.env`.
- **Human report in the export folder, machine trace in the repo** — HTML/PDF sit next to the markdown so you can open them with the files; `telemetry/` is for comparing methods (`make bench`) and finding bottlenecks, not for the document owner.

## Where the details live

- The code — `src/cli.py`, `src/batch/`, `src/converter/`, `src/agents/image_ocr.py`, `src/telemetry/`.
- Product how-to — [README.md](../README.md) (setup, formats, `src/config.py`).
- Agent skills — `.agents/skills/document-code`, `.agents/skills/makefile-operations`.
