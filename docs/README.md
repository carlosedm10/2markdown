# 2markdown — The One Doc

Local batch converter: a file or folder in, Markdown plus a JSON manifest out. The converter process runs in Docker. Optional vision OCR talks to **Ollama on the host** so GPU/Metal stay outside the container.

## The taxonomy

These names repeat in config, CLI flags, the manifest, and frontmatter. They *are* the mental model.

| Term | Covers |
|---|---|
| **batch** | Walk input, convert each file, soft-fail, write sibling `*_2markdown/` |
| **OCR backend** | `tesseract` (default, `eng+spa`) or `ollama` (host vision model); images and PDF pages both escalate to vision on low Tesseract confidence |
| **PDF page OCR** | Per-page native-text threshold; only weak pages are rasterized; Tesseract first, vision below `pdf_ocr_llm_min_confidence`; timeout cancels remaining pages |
| **figure** | A detected region of a PDF page — raster image or a cluster of vector strokes — rendered to PNG, linked inline under its `## Page N`, and optionally described by the vision model |
| **iWork bundle** | `.pages` / `.key` / `.numbers` as one unit; convert the bundled `preview.pdf` through the PDF pipeline, else OCR the raster preview as a flagged first page |
| **mind map** | `.xmind` zips convert from `content.json` into nested Markdown lists |
| **e-reader** | `.epub` / `.fb2` / `.mobi` / `.azw` / `.azw3` via `twomarkdown/converter/ereader.py` |
| **manifest** | `<output>/.2markdown-manifest.json` — status, `ocr_backend`, checksum, timing |
| **export report** | `<output>/2markdown-report.html` and `.pdf` — success, reliability, tools, timings |
| **trace / telemetry** | `.2markdown-trace.json` in the export folder; `telemetry/run-*.json` in the repo (`make process` sets `TWOMARKDOWN_TELEMETRY_DIR`) |

## How it's built

```
CLI (twomarkdown.cli) → paths → processor → walker (optional zip explode, magic-byte suffix)
                              ↓
         iWork | e-reader | eml | xlsx | legacy Office | MarkItDown
                              ↓
         PDF compose (pages, OCR prose, tables) -> figure regions inline | image OCR
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
- **OCR:** Tesseract first when hybrid is on. Standalone images and PDF pages both call Ollama when Tesseract confidence is low (`pdf_ocr_llm_min_confidence` for pages). Vision calls are one-at-a-time. Tiny images are skipped. Remote images are fetched only if enabled, capped at 8 MiB.

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
- **Magic bytes beat a lying extension** — a PNG named `Foto.pdf` is OCR'd as an image, not sent through the PDF pipeline.
- **LibreOffice ships in the image** — `.doc`/`.ppt`/`.xls`/`.odt`/`.rtf` convert via headless Writer/Calc/Impress; the image is larger because of that.
- **`--ocr-backend=ollama` implies LLM** — a second `--llm-enabled` gate caused silent Tesseract fallback.
- **PDF OCR is per page** — a mixed PDF with some extractable text used to skip scans entirely. OCR is prose under `## Page N`, not fenced code.
- **Native e-readers, not only MarkItDown** — EPUB spine order and FB2/MOBI needed their own module. EPUBs are copied onto local disk before parsing because Docker bind-mounts of iCloud Drive often break `zipfile` seek (`Bad Zip file`).
- **Ollama is not stopped by `make down`** — tearing down Docker must not kill a host daemon other tools use (`make stop-ollama` is explicit).
- **Tesseract then vision, on confidence not emptiness** — PDF page OCR used to keep any non-empty Tesseract text, so handwriting (confident nonsense) never reached the vision model and `--ocr-backend=ollama` changed nothing. Pages below `pdf_ocr_llm_min_confidence` are now re-OCR'd by the vision model, falling back to the Tesseract text if the model declines. One vision request is in flight at a time so parallel PDF workers cannot stampede host Ollama.
- **Figures are regions, not embedded images** — engineering slides draw plots and circuits as vector strokes, so `get_images()` returns nothing and the text layer keeps only loose axis ticks. `converter/figures.py` clusters vector drawings and raster rects into regions, renders each from the page, and places it under its own `## Page N` with an optional generated description. The flat trailing `## Embedded images` index is only emitted when the figure pass produced nothing.
- **A ruled box is not a table** — `find_tables()` reports the slide frame, so every slide's body was duplicated into a one-cell "table". Regions with fewer than two populated columns, an over-long cell, or near-page area are rejected, and tables are sorted by position because `find_tables()` ordering is not stable between runs.
- **PDF text artifacts are repaired, not passed through** — LaTeX PDFs emit ligatures and big math delimiters as C0 control codes and accents as separate glyphs, so "flujo" arrived as "\x1dujo" and "Módulo" as "M´odulo"; a stray NUL also made the `.md` file binary to git. `clean.repair_pdf_text_artifacts` maps the known codes, composes the accents, and drops anything still unmapped.
- **Make is still the process CLI** — `VERBOSE`, `DRY_RUN`, `WORKERS`, `FORCE`, `OCR_BACKEND`, `OUTPUT`, `NO_OCR`, and `EMIT_CHUNKS` are Make vars forwarded into the Typer CLI; converter knobs live in `twomarkdown/config.py`.
- **OCR engine lives in `.ocr-mode`** — `make build` / `make build ollama` write that gitignored file instead of rewriting `twomarkdown/config.py`.
- **iWork prefers `preview.pdf`, degrades to the raster preview** — no IWA parsers, Kreuzberg, or AppleScript in Docker. iCloud-synced bundles ship `preview.jpg` and no PDF, so those recover the first page only, under an explicit "vista previa parcial" banner telling the owner to export a PDF.
- **Installable package is `twomarkdown`** — imports are `twomarkdown.*`; the CLI entry is `python -m twomarkdown.cli`.
- **Settings in code, secrets in `.env`, OCR mode in `.ocr-mode`** — flags and tuning are Pydantic `BaseModel` defaults; `Secrets` is the only `BaseSettings` class.
- **Human report in the export folder, machine trace in the repo** — HTML/PDF sit next to the markdown so you can open them with the files; `telemetry/` is for comparing methods (`make bench`) and finding bottlenecks, not for the document owner.
- **Per-file timeout is conversion time, not queue time** — parallel batches only start the clock when a worker picks up the file. Timeout cancels remaining PDF pages so a zombie thread does not keep rasterizing or queuing Ollama. An in-flight vision call can still finish; it will not start new OCR.

## Where the details live

- The code — `twomarkdown/cli.py`, `twomarkdown/batch/`, `twomarkdown/converter/`, `twomarkdown/agents/image_ocr.py`, `twomarkdown/telemetry/`.
- Product how-to — [README.md](../README.md) (setup, formats, `twomarkdown/config.py`).
- Agent skills — `.agents/skills/document-code`, `.agents/skills/makefile-operations`.
