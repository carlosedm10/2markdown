# 2markdown — The One Doc

Local batch converter: a file or folder in, Markdown plus a JSON manifest out. The converter process runs in Docker. Optional vision OCR talks to **Ollama on the host** so GPU/Metal stay outside the container.

## The taxonomy

These names repeat in config, CLI flags, the manifest, and frontmatter. They *are* the mental model.

| Term | Covers |
|---|---|
| **batch** | Walk input, convert each file, soft-fail, write sibling `*_2markdown/` |
| **OCR backend** | `tesseract` (default, `eng+spa`) or `ollama` (host vision model) |
| **PDF page OCR** | Per-page native-text threshold; only weak pages are rasterized |
| **iWork bundle** | `.pages` / `.key` / `.numbers` as one unit, not files inside `Data/` |
| **e-reader** | `.epub` / `.fb2` / `.mobi` / `.azw` / `.azw3` via `src/converter/ereader.py` |
| **manifest** | `<output>/.2markdown-manifest.json` — `ok` / `failed` / `skipped` + `ocr_backend` |

## How it's built

```
CLI (src.cli) → paths → processor → walker
                              ↓
              iWork | e-reader | MarkItDown
                              ↓
              PDF page OCR | image OCR | enrich image refs
                              ↓
              frontmatter + .md + manifest
```

### The principles that matter

- **One file, one chance to fail.** The batch continues; the manifest stores the error.
- **Skip is semantic, not just mtime.** Failed files retry; changing OCR engine reconverts.
- **Host GPU, container CPU.** Tesseract lives in the image; Ollama stays on the host (`host.docker.internal`).
- **Make is the contract.** Package, lint, and test recipes exist once; CI flips transport with `CI=true`.

## How data flows

- **Reads:** a host path (file or tree). `make process` bind-mounts only that path and the sibling output directory.
- **Writes:** mirrored `.md` files (YAML frontmatter: `source`, `converted_at`, `ocr_backend`) and the manifest.
- **OCR:** Tesseract in-process, or PydanticAI → Ollama. Remote images are fetched only if enabled, capped at 8 MiB.

### Entities

- **FileRecord:** one source path, status, optional error, output path, OCR backend used.
- **BatchResult:** converted / failed / skipped counts plus failed paths (CLI exit 1 if any failed).

### One example, end to end

1. `make process INPUT=/docs/scan.pdf` (after `make fresh-setup` and `make build` or `make build ollama`).
2. CLI normalizes to batch root + default output `/docs/scan_2markdown/`.
3. MarkItDown converts the PDF; PyMuPDF measures each page; short pages are rasterized and OCR'd.
4. Markdown is written; the manifest records `ok` with `ocr_backend`.

## Key decisions and caveats (why it is this way)

- **Sibling output folder, not in-place overwrite** — avoids clobbering sources and makes skip-by-mtime possible.
- **`--ocr-backend=ollama` implies LLM** — a second `--llm-enabled` gate caused silent Tesseract fallback.
- **PDF OCR is per page** — a mixed PDF with some extractable text used to skip scans entirely.
- **Native e-readers, not only MarkItDown** — EPUB spine order and FB2/MOBI needed their own module.
- **Ollama is not stopped by `make down`** — tearing down Docker must not kill a host daemon other tools use (`make stop-ollama` is explicit).

## Where the details live

- The code — `src/cli.py`, `src/batch/`, `src/converter/`, `src/agents/image_ocr.py`.
- Product how-to — [README.md](../README.md) (setup, formats, env vars).
- Agent skills — `.agents/skills/document-code`, `.agents/skills/makefile-operations`.
