# 2markdown — Agent Instructions

Batch CLI that converts local files and folders to Markdown (Docker + optional host Ollama OCR). The installable package imports as `src.*`.

Read before changing anything:

1. [docs/README.md](docs/README.md) — taxonomy, architecture, data flows, key decisions. Keep its "Key decisions" list updated when a change makes or supersedes one.
2. [.agents/skills/makefile-operations/SKILL.md](.agents/skills/makefile-operations/SKILL.md) — every `make` / Docker / `uv` change.
3. [.agents/skills/document-code/SKILL.md](.agents/skills/document-code/SKILL.md) — documentation convention.

Known code flaws: [INCONSISTENCIES.md](INCONSISTENCIES.md).

## Non-negotiable rules

- **Makefile is the public interface** — never `uv add`/`uv lock`/`uv sync` on the host; use `make uv-add`, `make uv-lock`, `make lint`, `make test`.
- **Lifecycle verbs are compose verbs** — `up` / `down` / `build` / `restart`, not `start`/`stop`.
- **Docs ride the PR** — architecture, data-flow, or decision changes update `docs/README.md` in the same PR.
- **Soft-fail the batch** — one bad file must not abort the rest; record it on the manifest.

## Local verification

- `make lint` then `make test` (unit). `make test TEST=tests/foo.py` for one file. `make test-integration` needs Tesseract.
- GitHub Actions sets `CI=true` so those targets run `uv` natively; locally they use `docker compose run --rm`.
