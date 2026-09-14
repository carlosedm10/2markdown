# 2markdown — Agent Instructions

Batch CLI that converts local files and folders to Markdown (Docker + optional host Ollama OCR). The installable package imports as `twomarkdown.*`.

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

- `make lint` then `make test` (unit). `make test-backend TEST=tests/foo.py` for one backend file. `make test-integration` needs Tesseract.
- `lint`/`test` are ladders: `lint-backend` + `lint-frontend`, `test-backend` + `test-frontend`. The frontend half prints a `:: skipped` line instead of failing when `app/node_modules` isn't installed.
- GitHub Actions sets `CI=true` so those targets run `uv` natively; locally they use `docker compose run --rm`.

## Skills

Framework skills live in `.agents/skills/`; read a skill's `SKILL.md` (and `reference.md`) before applying it.

- **dockerization-template** — `compose.yaml` / `docker/*.Dockerfile` shape and checklist. Applies to `docker/backend.Dockerfile` and `compose.yaml` here; this repo has no Postgres and no frontend Dockerfile (see deviations below).
- **document-code** — the documentation convention this repo's `docs/README.md` and this file follow.
- **env-secrets** — `.envrc` / `.env_template` / `.gitignore` contract. Applies as-is: `.envrc` loads `.env` via direnv, `.env_template` documents provider keys, `.env` is gitignored.
- **fastapi-test-generation** — pytest conventions for FastAPI endpoints. Applies to `twomarkdown/server/`; this repo keeps tests in `tests/`, not per-app directories (see deviations below).
- **frontend-react** — scaffolding new React/Vite/Bun/shadcn webapps. Not run here — `app/` already exists; only consult it if `app/` is rebuilt from scratch.
- **github-actions** — CI workflows must call only `make <target>`. Applies to `.github/workflows/ci.yml`.
- **makefile-operations** — Makefile naming, ladders, CI/local parity. Applies to the root `Makefile`; see the ladder note above and the frontend skip-when-absent deviation below.
- **project-scaffold** — orchestrates a brand-new project from zero. Not applicable to an existing repo; reference only if a new deployable is added from scratch.
- **pydantic-ai-agents** — `Agent`/string-model-id/structured-output conventions. Applies to `twomarkdown/agents/*.py` and `twomarkdown/prompts.py`, which already follow it.

## Skills and deviations

- **backend-fastapi** not adopted — `twomarkdown/server/` is a package inside `twomarkdown/`, not a standalone FastAPI service directory, and there is no database.
- **frontend-react**'s shadcn/Tailwind not adopted — `app/` uses bespoke design tokens from `docs/mockups/desktop-v4.html`; `bunx shadcn init` is human-only and is the user's call, not scaffolded here.
- **fastapi-test-generation** tests live in `tests/` (repo convention: one flat test directory for the whole package), not per-feature directories.
- **fastapi-test-generation** prescribes `pytest-asyncio`; this repo's async tests (`tests/test_server.py`) are marked `@pytest.mark.anyio` instead and run on anyio's asyncio backend — `anyio` already ships as a transitive dependency of `fastapi`/`pydantic-ai`, so `pytest-asyncio` is not a dependency here.
- **dockerization-template**'s Postgres and frontend-Dockerfile sections not adopted — this repo has no database, and `app/` (the desktop UI) runs natively via `make app`/`make app-web` (needs the host GPU, host Ollama, and arbitrary user folders outside any bind mount — see `docs/README.md`'s Key decisions), not in a container.
- **makefile-operations**' "atomics never skip" rule is relaxed for `lint-frontend`/`test-frontend` only: they print a `:: skipped` line and exit 0 when `app/node_modules` is missing, and pass `--if-present` so an as-yet-undefined `lint`/`test` script in `app/package.json` (owned by `app/`, not touched here) also degrades instead of failing. `lint-backend`/`test-backend` still fail hard, as the rule requires.
