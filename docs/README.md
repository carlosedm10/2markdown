# 2markdown — The One Doc

Local batch converter: a file or folder in, Markdown plus a JSON manifest out. The converter process runs in Docker. Optional vision OCR talks to **Ollama on the host** so GPU/Metal stay outside the container.

## The taxonomy

These names repeat in config, CLI flags, the manifest, and frontmatter. They *are* the mental model.

| Term | Covers |
|---|---|
| **batch** | Walk input, convert each file, soft-fail, write sibling `*_2markdown/` |
| **OCR backend** | `tesseract` (default, `eng+spa`) or `ollama` (host vision model); hybrid uses Tesseract confidence then vision |
| **PDF page OCR** | Per-page native-text threshold; only weak pages are rasterized; tables and page headings stay in reading order |
| **iWork bundle** | `.pages` / `.key` / `.numbers` as one unit; convert the bundled `preview.pdf` through the PDF pipeline |
| **e-reader** | `.epub` / `.fb2` / `.mobi` / `.azw` / `.azw3` via `twomarkdown/converter/ereader.py` |
| **manifest** | `<output>/.2markdown-manifest.json` — status, `ocr_backend`, checksum, timing |

## How it's built

```
CLI (twomarkdown.cli) → paths → processor → walker (optional zip explode, magic-byte suffix)
                              ↓
         iWork | e-reader | eml | xlsx | legacy Office | MarkItDown
                              ↓
         PDF compose (pages, OCR prose, tables, assets) | image OCR / figures
                              ↓
         clean markdown → frontmatter + .md + manifest [+ optional .chunks.json]
```

### The principles that matter

- **One file, one chance to fail.** The batch continues; the manifest stores the error.
- **Skip is semantic, not just mtime.** Failed files retry; changing OCR engine reconverts.
- **Host GPU, container CPU.** Tesseract lives in the image; Ollama stays on the host (`host.docker.internal`).
- **Make is the contract.** Package, lint, and test recipes exist once; CI flips transport with `CI=true`.

## How data flows

- **Reads:** a host path (file or tree). `make process` bind-mounts only that path and the sibling output directory.
- **Writes:** mirrored `.md` files (YAML frontmatter includes `source`, `ocr_backend`, `title`, `ocr_pages`, `tables`, `language`, `char_count`), the manifest (checksum + duration), optional `_assets/` and `.chunks.json`. OCR text is cached under the output dir.
- **OCR:** Tesseract first when hybrid is on; low confidence or empty text can call Ollama. Tiny images are skipped. Remote images are fetched only if enabled, capped at 8 MiB.

### Entities

- **FileRecord:** one source path, status, optional error, output path, OCR backend, checksum, duration, char count.
- **BatchResult:** converted / failed / skipped counts, failed paths, and `planned` paths for `--dry-run`.

### One example, end to end

1. `make process INPUT=/docs/scan.pdf` (after `make fresh-setup` and `make build` or `make build ollama`).
2. CLI normalizes to batch root + default output `/docs/scan_2markdown/`.
3. MarkItDown converts the PDF; PyMuPDF measures each page; short pages are rasterized and OCR'd; tables become GFM under `### Table (page N)`.
4. Markdown is cleaned and written with richer frontmatter; the manifest records `ok` with `ocr_backend` and checksum.

## Key decisions and caveats (why it is this way)

- **Sibling output folder, not in-place overwrite** — avoids clobbering sources and makes skip-by-mtime possible.
- **`--ocr-backend=ollama` implies LLM** — a second `--llm-enabled` gate caused silent Tesseract fallback.
- **PDF OCR is per page** — a mixed PDF with some extractable text used to skip scans entirely. OCR is prose under `## Page N`, not fenced code.
- **Native e-readers, not only MarkItDown** — EPUB spine order and FB2/MOBI needed their own module.
- **Ollama is not stopped by `make down`** — tearing down Docker must not kill a host daemon other tools use (`make stop-ollama` is explicit).
- **Tesseract then vision** — hybrid OCR spends GPU only when Tesseract confidence is low.
- **Make is still the process CLI** — `VERBOSE`, `DRY_RUN`, `WORKERS`, `FORCE`, `OCR_BACKEND`, `OUTPUT`, `NO_OCR`, and `EMIT_CHUNKS` are Make vars forwarded into the Typer CLI; converter knobs live in `twomarkdown/config.py`.
- **OCR engine lives in `.ocr-mode`** — `make build` / `make build ollama` write that gitignored file instead of rewriting `twomarkdown/config.py`.
- **iWork is `preview.pdf` only** — no IWA parsers, Kreuzberg, or AppleScript in Docker. Bundles without a preview soft-fail.
- **LibreOffice is in the image** — `.doc` / `.ppt` / `.xls` / `.odt` / `.rtf` convert via `soffice`.
- **Installable package is `twomarkdown`** — imports are `twomarkdown.*`; the CLI entry is `python -m twomarkdown.cli`.
- **Settings in code, secrets in `.env`, OCR mode in `.ocr-mode`** — flags and tuning are Pydantic `BaseModel` defaults; `Secrets` is the only `BaseSettings` class.

## Where the details live

- The code — `twomarkdown/cli.py`, `twomarkdown/batch/`, `twomarkdown/converter/`, `twomarkdown/agents/image_ocr.py`.
- Product how-to — [README.md](../README.md) (setup, formats, `twomarkdown/config.py`).
- Agent skills — `.agents/skills/document-code`, `.agents/skills/makefile-operations`.
