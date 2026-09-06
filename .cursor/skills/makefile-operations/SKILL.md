---
name: makefile-operations
description: Manages Makefile-driven Docker Compose workflows with the fractal per-deployable hierarchy, bare-verb naming and scope ladders, compose-named lifecycle, logs/terminal ladders, graceful aggregates vs strict atomics, CI/local parity via a transport switch, and package manager tasks. Use when editing/running Makefile targets, naming targets, troubleshooting compose commands, managing backend/frontend dependencies, standardizing local dev commands, wiring GitHub Actions to make, or reviewing Makefile PRs.
---

# Makefile Operations

## Scope

Use this skill when working on repositories that orchestrate development tasks from `Makefile`s,
especially with Docker Compose services like backend, frontend, and database.

## Core Rules

0. The hierarchy is FRACTAL — see **Fractal Makefile Hierarchy**. Every deployable owns a
   Makefile with bare verbs; parents are pure orchestration.
1. Prefer existing `Makefile` targets over ad-hoc shell commands.
2. Name lifecycle targets after the Docker Compose command they wrap — `up`, `down`, `build`,
   `restart` — never invented synonyms like `start`/`stop`. A target that wraps one compose
   command should be guessable from the compose command.
3. **A bare verb is always the widest scope**, and every verb forms a ladder:
   `test-backend → test`. The same verb means the same thing at every layer.
4. Keep lifecycle ordering consistent:
   - Up/build before exec
   - Exec for running containers
   - Run `--rm` for one-off jobs
   - `down` with `--remove-orphans` when tearing down
5. Keep backend and frontend package managers isolated:
   - Backend: `uv` in backend container
   - Frontend: `bun` in frontend container
6. Use service-specific logs and shells instead of broad noisy commands.
7. **Aggregates degrade, atomics don't** — see **Aggregates vs Atomics**.
8. If a command can be destructive, call it out clearly before proposing/running it.
9. **CI parity**: GitHub Actions must call `make <target>` — never duplicate lint/test/build
   commands in workflow YAML. If CI needs a step, add a Makefile target first.
10. **Target names are a public contract.** Everything outside the Makefile that drives the
    ladder — CI workflows, dev-environment orchestrators, the README, teammates' muscle
    memory — breaks on a rename. Treat renames as breaking changes and update every caller in
    the same change.

## Fractal Makefile Hierarchy

The repo's Makefiles form a tree that mirrors the directory tree, and the same three rules
repeat at every level:

```
Makefile                    ← root: PURE orchestration, no recipes of its own
├── frontend/Makefile       ← bare verbs: build, up, down, logs, lint, test, shell
└── backend/Makefile        ← orchestrates its children: bare = all, <verb>-api = one
    ├── api/Makefile        ← bare verbs + identity vars, includes the shared engine
    ├── worker/Makefile     ← same
    └── makefiles/*.mk      ← shared engine for sibling stacks (recipes written ONCE)
```

1. **Bare verbs at your own level.** Inside `frontend/`, the frontend build is `make build` —
   never `build-frontend`. The scope suffix exists only one level up, where it addresses a
   child: `build-frontend: $(MAKE) -C frontend build`. A parent Makefile contains forwards
   and cross-child ordering (e.g. the backend migrated before the frontend's codegen), never a
   child's recipes.
2. **Siblings share an engine, not copies.** When two children are the same shape (two Python
   services, two Node apps), their recipes live once in an included `*.mk`; each child's
   Makefile is just identity variables + `include`. Orchestrator Makefiles answer "which
   children"; the engine answers "how a child works inside".
3. **Each level announces its own phases** with `@echo ":: <verb>: <path>"` as the first
   recipe line (`:: up: backend/api`). The parent never narrates a child's work — narration
   travels with the recipe, so every caller (a human, the root Makefile, CI, an external
   orchestrator) sees the same progress markers for free.

**Why per-deployable Makefiles instead of one root file:** the deployable's Makefile travels
with the deployable's code, so the same shortcuts work in every context where that code
exists —

- **dev machine**: `cd frontend && make build`;
- **CI**: `working-directory: backend/api` + `run: make test` — the workflow step IS the
  local command (pair with the transport switch below: same recipe runs via docker compose
  locally and natively when `CI=true`);
- **inside a running container/pod**: shelling into a deployed service gives you
  `make shell`, `make logs-app` — the ops shortcuts ship with the image.

The pod case adds one constraint: **everything a deployable's Makefile `include`s must live
inside that deployable's Docker build context**, or the include must be guarded. An engine
`.mk` that sits above the build context never makes it into the image, and `make` inside the
pod dies on the missing include:

```makefile
ifneq (,$(wildcard ../makefiles/service.mk))
include ../makefiles/service.mk
else
shell:                         # in-pod fallback: the engine never ships with the image
	sh
endif
```

## Naming

- **Lifecycle verbs are the Docker Compose commands they wrap**: `up`, `down`, `build`,
  `restart` — never `start`/`stop`.
- **Leading token = what you group by.** Subsystem-first where one subsystem owns the target
  (`db-seed`, `db-load-schema`, `types-check`, `bun-add`, `uv-lock`), otherwise verb-first
  (`test-backend`, `lint-frontend`, `build-icons`). One deliberate exception: **`migrate`
  stays bare** — not `db-migrate` — because it's the verb every framework's docs already use,
  and it carries its own ladder (`cd backend/api && make migrate` → `migrate` in `backend/`
  → root).
- **Bare verb = widest scope**, suffixes narrow it: `test-api → test-backend → test`.
- **Logs follow the ladder, no `show-` prefix**: `logs`, `logs-backend`, `logs-frontend`,
  `logs-db` at the root; `logs`, `logs-tail`, `logs-app`, `logs-db` inside a stack.
  **App logs never include the databases** — those are `logs-db` only.
- **Terminals**: at the root `<scope>-shell` (`backend-shell`, `frontend-shell`,
  `postgres-shell`); inside a component just `shell` (plus `console` for a framework REPL).
- **Variants are suffixes** (`test-watch`, `coverage-html`, `logs-tail`); a
  single-item run is a **variable**, not a target (`make test TEST=path`,
  `make bun-add PKG=name`).
- **`lint` checks; `format` rewrites style; `lint-fix` applies auto-fixable lint.** Three
  verbs, each with a ladder (`format-backend` → `format`, `lint-fix-backend` → `lint-fix`).
  Do not fold `ruff check --fix` into `format`, and do not alias `format` to `lint-fix`.
- **Database targets are named for the operation, not the occasion.** `migrate` is the base
  (create + migrate + that service's idempotent local tasks) and its variants suffix it:
  `migrate-seed`. Anything that loads a schema instead of replaying migrations says so —
  `db-load-schema`, `db-load-schema-test` — because "setup" hides which of the two you get,
  and the test-env one is what CI calls before the test suite. Never name a target after the
  phase it serves (`db-setup`, `db-bootstrap`, `db-init`): the next reader can't tell whether
  it migrates, loads, seeds, or all three.

## Aggregates vs Atomics

- **Combined targets degrade.** Aggregates (`lint`, `test`, `format`, and their `-backend` /
  `-frontend` variants when they fan out) probe each stack with a silent readiness target and
  skip what isn't running — but they fail when a check fails **and** when *nothing* could run
  (a silent no-op that reports success is worse than an error).
- **Atomic targets never skip.** `lint-frontend`, `test-backend` run the check or fail,
  because CI calls exactly those and a skip there is a false green.

```makefile
backend-ready:                 # silent probe, no output on failure
	@docker compose ps --status running --services | grep -qx backend-PROJECT_SLUG
```

## Command Ordering Standard

Follow this order unless the user asks otherwise:

1. **Bring stack up**
   - `make build` for rebuilds
   - `make up` for normal startup
2. **Run app tasks**
   - `make migrate` (bare verb; recipes live in **backend-drf** or **backend-fastapi**)
   - `make format` (style) then `make lint-fix` (auto-fix) then `make lint` (check)
   - Package changes (`uv-*`, `bun-*`)
3. **Inspect**
   - `make logs-backend`, `make logs-frontend`, `make logs-db` (`make logs` for everything)
   - `make backend-shell` / `make postgres-shell` for deeper checks
4. **Teardown**
   - `make down` (must map to `docker compose down --remove-orphans`)

If a workflow needs one-off commands, keep them deterministic and container-scoped.

## CI/local parity

GitHub Actions call `make <target>`. The transport is a **mode, not a fork**: the engine's
command wrappers run inside the service container locally and **natively when `CI=true`**
(GitHub sets it) or `NATIVE=1`, so the recipe itself is written once.

```makefile
ifneq ($(filter true 1,$(CI) $(NATIVE)),)
define run_backend
cd $(SERVICE_ROOT) && $(1)
endef
else
define run_backend
docker compose run --rm backend-PROJECT_SLUG sh -c '$(1)'
endef
endif
```

Every scaffolded project must expose these aggregate targets for GitHub Actions:

| Target | Purpose |
|--------|---------|
| `lint` | Runs `lint-backend` + `lint-frontend` |
| `lint-backend` | `uv run ruff check` in backend container |
| `lint-frontend` | `bun run lint` in frontend container |
| `test` | Runs `test-backend` + `test-frontend` |
| `test-backend` | stack test runner in backend container (see backend skill) |
| `test-frontend` | frontend test command (if configured) |
| `build` | `docker compose build` |
| `db-load-schema-test` | loads the schema in the test env, before the suite |

GitHub Actions workflows call only make targets. Keep a step raw in YAML only when it is
genuinely CI-shaped (changed-files linting, runner bootstrap quirks, native steps on runners
with no compose stack) — and say so in a comment. See the **github-actions** skill.

Coverage is a suffix on the stack runner (`coverage`, `coverage-html`), not a second `tests`
target. Do not add `tests` alongside `test` / `test-backend`. Filtered runs use `TEST=`,
never a duplicate target. Recipes for the Django test runner and `coverage` live in
**backend-drf**.

## Docker Compose Guidance

### Startup

- Use detached mode for standard development startup.
- Use force recreate on full stack restart when consistency matters.
- Prefer one canonical startup target (`build` or `up`) instead of multiple overlapping variants.
  Never add `start`/`stop` aliases.
- After `up` and `build`, echo the local URLs from the backend skill (Django admin/docs vs
  FastAPI OpenAPI), plus the frontend origin when a frontend exists.

### Teardown

- Always include `--remove-orphans` on `down`.
- **`clean` is NUCLEAR**: `docker compose down --volumes --remove-orphans`, named DB volumes
  included. Clean means clean — `make up` + `make migrate` rebuilds from zero. Don't soften
  it, and don't hide a deeper prune behind an innocent name.
- **`clean-builder`** is the extra step: `docker builder prune -f`. Do not fold it into `clean`.
- Never add host-wide `docker stop/rm/rmi $(docker ps …)` targets. Those are not project-scoped.
- Keep cleanup idempotent with `|| true` only where failure is expected and harmless.

### Run vs Exec

- Use `docker compose exec` for commands inside already-running containers.
- Use `docker compose run --rm` for one-off commands where container lifecycle should end
  immediately.
- For CI/non-interactive contexts, use `exec -T` to avoid TTY issues.

## Terminal and Logs Conventions

- Provide dedicated `*-shell` targets per critical service at the root; inside a component the
  target is just `shell` (and `console` for a framework REPL).
- Logs follow the same verb ladder as the lifecycle targets: `logs-<scope>` per service, bare
  `logs` for everything — never a `show-` prefix. App logs never include the databases; give
  those their own `logs-db`.
- Avoid defaulting to global `docker compose logs -f` unless explicitly requested.
- When debugging:
  1. reproduce with the specific target
  2. stream service logs
  3. open service shell if needed
  4. rerun narrowly scoped command

## Standard Section Banners

Use standardized comment banners for `Makefile` sections. Keep spacing, dashes, and
capitalization consistent.

Use this exact format:

```makefile
# ----------------------------- Section Name ----------------------------- #
```

Required convention:

- One blank line before each section banner (except at file start).
- One blank line after each banner before targets.
- Use title case for section names.
- Keep one section per concern (Docker Compose, package managers, terminals, debugging, testing, danger zone).
- Preserve and reuse this exact debugging header:

```makefile
# ----------------------------- Debugging ----------------------------- #
```

Suggested section set for this repository style:

```makefile
# ------------------------------ Docker Compose ------------------------------ #
# ----------------------------- Backend Package Management ----------------------------- #
# ----------------------------- Frontend Package Management ----------------------------- #
# ----------------------------- Terminals ----------------------------- #
# ----------------------------- Debugging ----------------------------- #
# ----------------------------- Django ----------------------------- #
# ----------------------------- FastAPI / Alembic ----------------------------- #
# ----------------------------- Code Formatting ----------------------------- #
# ----------------------------- Testing ----------------------------- #
# ----------------------------- ⛔️ DANGER ZONE ⛔️ ----------------------------- #
```

Include **Django** or **FastAPI / Alembic** based on backend type — not both unless the
project genuinely uses both. Copy the recipes from **backend-drf** or **backend-fastapi**;
this skill does not own `manage.py` / Alembic recipes. Destructive targets live in the
danger zone at the bottom.

## Section `.PHONY` and Ordering Rules

After each section banner, add a `.PHONY` line that lists all targets in that section, in the
exact same order they are defined below.

Rules:

- Place `.PHONY` immediately under the section banner (after optional section comments like `# Usage`).
- Keep target order in `.PHONY` synchronized with target declaration order.
- Update `.PHONY` every time targets are added, removed, or reordered.
- Do not include targets from other sections in the same `.PHONY`.

Pattern:

```makefile
# ----------------------------- Code Formatting ----------------------------- #
.PHONY: lint-backend lint-frontend lint format-backend format-frontend format lint-fix-backend lint-fix-frontend lint-fix
```

### Simple to Combined convention

Define targets from simple to complex:

- First: atomic targets (single service / single responsibility), e.g. `lint-backend`, `lint-frontend`.
- Last: combined/aggregate target that orchestrates previous ones, e.g. `lint`.

Example:

```makefile
# Usage:
#   make format-backend / make format-frontend / make format
#   make lint-fix-backend / make lint-fix-frontend / make lint-fix
#   make lint
lint-backend:
	docker compose exec -T backend-PROJECT_SLUG sh -c "uv run ruff check ."

lint-frontend:
	docker compose exec -T frontend-PROJECT_SLUG sh -c "bun run lint"

lint:
	make lint-backend
	make lint-frontend

format-backend:
	docker compose exec -T backend-PROJECT_SLUG sh -c "uv run ruff format ."

format-frontend:
	docker compose exec -T frontend-PROJECT_SLUG sh -c "bun run format"

format:
	make format-backend
	make format-frontend

lint-fix-backend:
	docker compose exec -T backend-PROJECT_SLUG sh -c "uv run ruff check --fix ."

lint-fix-frontend:
	docker compose exec -T frontend-PROJECT_SLUG sh -c "bun run lint --fix"

lint-fix:
	make lint-fix-backend
	make lint-fix-frontend
```

Three aggregates, each with two atomics:

| Aggregate | Backend | Frontend |
|-----------|---------|----------|
| `format` | `ruff format` | `bun run format` |
| `lint-fix` | `ruff check --fix` | `bun run lint --fix` |
| `lint` | `ruff check` | `bun run lint` |

Append extra style tools (Prettier already in `format`, i18n key sort, …) to `format-frontend`.
Scaffolds must ship a `format` script in `package.json`.

## Backend operations by stack

This skill is stack-agnostic. Splice one section into the Makefile from the backend skill:

| Backend | Skill | Makefile section |
|---------|-------|------------------|
| Django / DRF | **backend-drf** | `# ----------------------------- Django ----------------------------- #` |
| FastAPI | **backend-fastapi** | `# ----------------------------- FastAPI / Alembic ----------------------------- #` |

Keep `migrate` as the bare verb at every layer. Do not duplicate those recipes here.

## Package Manager Conventions

### Backend (`uv`)

- Keep lockfile actions explicit: lock, add/remove, full upgrade vs single-package upgrade, lock regenerate/refresh.
- Run backend package commands in backend service context, usually with `run --rm`.

### Frontend (`bun`)

- Run bun commands in frontend service context.
- Keep lockfile regeneration explicit and reproducible.
- Pair frontend formatting and project-specific checks in one target when practical.

## Package Management Target Patterns

### Required operations

Every package-management section should provide explicit targets for:

- lock / freeze dependencies
- add dependency
- update all dependencies
- update one dependency
- remove dependency
- regenerate or refresh lockfile

### Variable conventions

- Use `PKG` for the package name input.
- Support both:
  - no `PKG` -> bulk update
  - with `PKG` -> single-package update
- Document usage immediately above the targets with short examples.

### Concrete example (`uv`)

```makefile
.PHONY: uv-lock uv-add uv-update uv-remove uv-lock-regenerate

# Usage:
#   make uv-add PKG="package[extras]==version"
#   make uv-update            # update all
#   make uv-update PKG=foo    # update specific package
#   make uv-remove PKG=foo
#   make uv-lock-regenerate   # refresh lock from scratch
uv-lock:
	docker compose run --rm backend-PROJECT_SLUG uv lock

uv-add:
	docker compose run --rm backend-PROJECT_SLUG uv add $(PKG)

uv-update:
ifeq ($(PKG),)
	docker compose run --rm backend-PROJECT_SLUG uv lock --upgrade
else
	docker compose run --rm backend-PROJECT_SLUG uv lock --upgrade-package $(PKG)
endif

uv-remove:
	docker compose run --rm backend-PROJECT_SLUG uv remove $(PKG)

uv-lock-regenerate:
	docker compose run --rm backend-PROJECT_SLUG uv lock --refresh
```

### Concrete example (`bun`)

```makefile
.PHONY: bun-install bun-add bun-update bun-remove bun-lock-regenerate

# Usage:
#   make bun-install
#   make bun-add PKG=package
#   make bun-update PKG=package
#   make bun-remove PKG=package
#   make bun-lock-regenerate
bun-install:
	docker compose exec -T frontend-PROJECT_SLUG bun install

bun-add:
	docker compose exec -T frontend-PROJECT_SLUG bun add $(PKG)

bun-update:
	docker compose exec -T frontend-PROJECT_SLUG bun update $(PKG)

bun-remove:
	docker compose exec -T frontend-PROJECT_SLUG bun remove $(PKG)

bun-lock-regenerate:
	docker compose exec -T frontend-PROJECT_SLUG bun install --lockfile-only
```

Frontend is **bun**, not npm. If a legacy repo uses npm, mirror the same verbs (`npm-add`,
`npm-install`, …) rather than mixing managers in one Makefile.

## Target Design Checklist

When creating/updating `Makefile` targets:

- [ ] Bare verbs inside a deployable; scope suffixes only in the orchestrator above it.
- [ ] Lifecycle names = compose commands (`up`/`down`/`build`); `down --remove-orphans`.
- [ ] Ladder complete and bare verb = widest scope; `.PHONY` in sync per section.
- [ ] Correct `run --rm` vs `exec` (and `exec -T` for CI).
- [ ] Logs exclude the databases; `logs-db` exists where a database does; no `show-` prefix.
- [ ] `:: phase` announcements owned by each level, not the parent.
- [ ] Shared recipes in an included engine, guarded if it sits outside the build context.
- [ ] Aggregates degrade and fail when nothing ran; atomics never skip.
- [ ] `lint`, `test`, `build` aggregates present; CI calls make targets, transport-aware.
- [ ] DB targets named for the operation (`migrate`, `db-load-schema`), never the occasion.
- [ ] Destructive targets in the danger zone; `clean` documented as nuclear.
- [ ] Any renamed target: every external caller (CI, orchestrators, docs) updated in the
      same change.
- [ ] Command safe for repeated local runs; user-facing echoes concise.

## Recommended Review Flow

When asked to review a `Makefile`:

1. Verify the fractal shape: does each deployable own its Makefile, are parents pure
   orchestration, are sibling recipes shared in one engine?
2. Verify naming: compose-named lifecycle, bare verbs, complete ladders, suffix variants,
   operation-named DB targets.
3. Verify startup/teardown ordering, orphan handling, and that `clean`'s blast radius matches
   its name.
4. Verify `run` vs `exec` usage per target.
5. Verify package manager boundaries (`uv` backend, `bun` frontend).
6. Verify CI-facing targets exist, are used by GitHub Actions, and work under the transport
   switch (`CI=true`).
7. Verify aggregate degradation vs atomic strictness.
8. Verify logs/shell targets cover all core services and exclude DBs from app logs.
9. Flag duplicate or conflicting targets and suggest consolidation.
10. Flag renames that break external callers (CI, orchestrators, docs).

## Additional resources

- Full Makefile starter templates: [reference.md](reference.md)
- Django `manage.py` targets: **backend-drf**
- Alembic targets: **backend-fastapi**
