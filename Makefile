OLLAMA_MODEL ?= moondream
OLLAMA := $(if $(filter ollama,$(MAKECMDGOALS)),1,$(if $(OLLAMA),$(OLLAMA),0))
SERVICE := backend-twomarkdown
.DEFAULT_GOAL := help

# Dummy goal so `make build ollama` works (Make has no --flags)
ollama: ; @:

# Native transport when GitHub Actions sets CI=true, or NATIVE=1 locally.
ifneq ($(filter true 1,$(CI) $(NATIVE)),)
define run_uv
uv $(1)
endef
# Same transport as run_uv; test gets its own macro so the ulimit fix below
# (Docker-only) doesn't leak into every other run_uv caller.
define run_uv_test
uv $(1)
endef
else
define run_uv
docker compose run --rm $(SERVICE) uv $(1)
endef
# `ulimit -c 0` disables core dumps for this invocation only: pytest segfaults
# at interpreter teardown *after* the whole suite has already passed (see
# INCONSISTENCIES.md), and the container was leaving a 560 MB core file at the
# repo root on every run. This contains the symptom; it does not fix it.
define run_uv_test
docker compose run --rm $(SERVICE) sh -c 'ulimit -c 0 && uv $(1)'
endef
endif

# Frontend package manager for app/ — read app/package.json at need; kept as
# a variable so a switch to another package manager is a one-line change.
FRONTEND_PM ?= bun

# ------------------------------ Help ------------------------------ #
.PHONY: help

help:
	@echo "2markdown — available targets"
	@echo ""
	@echo "Setup:"
	@echo "  make fresh-setup              Copy secrets template to .env and tear down stack"
	@echo "  make build                    Build converter image (Tesseract OCR, default)"
	@echo "  make build ollama             Build image, start host Ollama, pull moondream"
	@echo "  make build ollama OLLAMA_MODEL=llava   Pull a different vision model"
	@echo "  make up                       Start backend container (+ host Ollama if enabled)"
	@echo "  make down                     Stop Docker containers (compose down --remove-orphans)"
	@echo "  make restart                  Restart the backend container"
	@echo "  make stop-ollama              Stop host Ollama"
	@echo "  make export-iwork INPUT=\"/path\"  Pre-export iWork via Pages.app (only if LibreOffice fails)"
	@echo "  make process INPUT=\"/path\" [VERBOSE=1] [DRY_RUN=1] [WORKERS=n]"
	@echo "            [FORCE=1] [OCR_BACKEND=tesseract|ollama] [OUTPUT=/path]"
	@echo "            [NO_OCR=1] [EMIT_CHUNKS=1] [IWORK_EXPORT=1]"
	@echo "            [DESCRIBE_FIGURES=1] [FIGURE_MODEL=qwen2.5vl:7b]"
	@echo "  make validate INPUT=\"/path_2markdown\"   Deterministic quality gate over converted markdown"
	@echo "  make judge INPUT=\"/path_2markdown\"      LLM review queue for implausible maths (needs Ollama)"
	@echo ""
	@echo "Backend package management:"
	@echo "  make uv-lock                  Refresh uv.lock"
	@echo "  make uv-add PKG=\"pkg==1.0\"    Add a dependency"
	@echo "  make uv-update                Upgrade all dependencies"
	@echo "  make uv-update PKG=foo        Upgrade one package"
	@echo "  make uv-remove PKG=foo        Remove a dependency"
	@echo "  make uv-sync EXTRA=foo         Install optional extra (plus dev)"
	@echo ""
	@echo "Terminals:"
	@echo "  make backend-shell            Open a shell in the backend container"
	@echo ""
	@echo "Desktop app (native host, see docs/README.md Key decisions):"
	@echo "  make app                      Run API (uv, background) + web UI (bun) in one terminal"
	@echo "  make app-down                  Kill this repo's own uvicorn/vite dev processes"
	@echo "  make app-web                   bun dev only (app/, port 5173)"
	@echo "  make app-build                 bun build (app/, production bundle)"
	@echo ""
	@echo "Debugging:"
	@echo "  make logs                     Tail backend logs"
	@echo ""
	@echo "Code quality:"
	@echo "  make format                   ruff format"
	@echo "  make lint-fix                 ruff check --fix"
	@echo "  make lint                     lint-backend + lint-frontend (frontend skips if not installed)"
	@echo "  make lint-backend             ruff check"
	@echo "  make lint-frontend            app/ package-manager lint"
	@echo ""
	@echo "Testing:"
	@echo "  make test                     test-backend + test-frontend (frontend skips if not installed)"
	@echo "  make test-backend             Unit tests (exclude integration)"
	@echo "  make test-backend TEST=tests/foo.py   Run a specific test path"
	@echo "  make test-frontend            app/ package-manager test"
	@echo "  make test-integration         Integration tests"
	@echo "  make bench                    Compare conversion methods (workers, OCR)"
	@echo ""
	@echo "Danger zone:"
	@echo "  make clean                    NUCLEAR: compose down --volumes --remove-orphans + caches"
	@echo "  make clean-builder            clean + docker builder prune"

# ------------------------------ Docker Compose ------------------------------ #
.PHONY: fresh-setup build up restart process down stop-ollama export-iwork

# Reset secrets file and tear down stack. Run once on a new machine.
# Feature flags live in twomarkdown/config.py; .env is credentials only.
fresh-setup:
	@echo ":: fresh-setup: ."
	cp .env_template .env
	$(MAKE) down
	@echo "Ready. Next: make build   OR   make build ollama (requires Ollama on host)"

# Build the converter image.
#   make build          -> Tesseract OCR (default, no extra downloads)
#   make build ollama   -> also ensure host Ollama and pull moondream (~2 GB RAM)
#   make build ollama OLLAMA_MODEL=llava   -> pull a different vision model
build:
	@echo ":: build: ."
	@test -f .env || (echo "Run make fresh-setup first." && exit 1)
	docker compose build
ifeq ($(OLLAMA),1)
	@python3 scripts/set_ocr_mode.py ollama $(OLLAMA_MODEL)
	@python3 scripts/ollama_host.py ensure
	@python3 scripts/ollama_host.py pull $(OLLAMA_MODEL)
	@echo "Ollama OCR ready ($(OLLAMA_MODEL))."
else
	@python3 scripts/set_ocr_mode.py tesseract
	@echo "Tesseract OCR ready."
endif

up:
	@echo ":: up: backend"
	@test -f .env || (echo "Run make fresh-setup && make build first." && exit 1)
	docker compose up -d $(SERVICE)
	@if python3 scripts/set_ocr_mode.py is-llm; then \
		python3 scripts/ollama_host.py ensure; \
	fi
	@echo "Stack up (container $(SERVICE)). Convert with: make process INPUT=..."

restart:
	@echo ":: restart: backend"
	docker compose restart $(SERVICE)

# Convert a file or folder on your machine.
# Usage: make process INPUT="/path/to/file-or-folder" [VERBOSE=1] [DRY_RUN=1] [WORKERS=n]
#        [FORCE=1] [OCR_BACKEND=tesseract|ollama] [OUTPUT=/path] [NO_OCR=1] [EMIT_CHUNKS=1]
#
# Output is written next to the input unless OUTPUT= is set:
#   /docs/reports     -> /docs/reports_2markdown/
#   /docs/report.pdf  -> /docs/report_2markdown/
process:
	@echo ":: process: backend"
	@test -f .env || (echo "Run make fresh-setup && make build first." && exit 1)
	@test -n "$(INPUT)" || (echo 'Usage: make process INPUT="/path/to/file-or-folder"' && exit 1)
	@set -e; \
	INPUT_ABS=$$(cd "$$(dirname "$(INPUT)")" && pwd)/$$(basename "$(INPUT)"); \
	test -e "$$INPUT_ABS" || (echo "Not found: $$INPUT_ABS" && exit 1); \
	WORK_DIR=$$(dirname "$$INPUT_ABS"); \
	if [ -n "$(OUTPUT)" ]; then \
		mkdir -p "$(OUTPUT)"; \
		OUTPUT_ABS=$$(cd "$(OUTPUT)" && pwd); \
	elif [ -d "$$INPUT_ABS" ]; then \
		OUTPUT_ABS="$$WORK_DIR/$$(basename "$$INPUT_ABS")_2markdown"; \
	else \
		BASENAME=$$(basename "$$INPUT_ABS"); \
		STEM=$${BASENAME%.*}; \
		OUTPUT_ABS="$$WORK_DIR/$${STEM}_2markdown"; \
	fi; \
	mkdir -p "$$OUTPUT_ABS"; \
	if [ "$(IWORK_EXPORT)" = "1" ]; then \
		python3 scripts/export_iwork.py "$$INPUT_ABS" --if-any; \
	fi; \
	if python3 scripts/set_ocr_mode.py is-llm || [ "$(OCR_BACKEND)" = "ollama" ]; then \
		python3 scripts/ollama_host.py ensure; \
	fi; \
	VERBOSE_FLAG=""; \
	if [ "$(VERBOSE)" = "1" ]; then VERBOSE_FLAG="-v"; fi; \
	DRY_RUN_FLAG=""; \
	if [ "$(DRY_RUN)" = "1" ]; then DRY_RUN_FLAG="--dry-run"; fi; \
	WORKERS_FLAG=""; \
	if [ -n "$(WORKERS)" ]; then WORKERS_FLAG="--workers $(WORKERS)"; fi; \
	FORCE_FLAG=""; \
	if [ "$(FORCE)" = "1" ]; then FORCE_FLAG="--force"; fi; \
	OCR_FLAG=""; \
	if [ "$(NO_OCR)" = "1" ]; then OCR_FLAG="--no-ocr"; fi; \
	OCR_BACKEND_FLAG=""; \
	if [ -n "$(OCR_BACKEND)" ]; then OCR_BACKEND_FLAG="--ocr-backend $(OCR_BACKEND)"; fi; \
	CHUNKS_FLAG=""; \
	if [ "$(EMIT_CHUNKS)" = "1" ]; then CHUNKS_FLAG="--emit-chunks"; fi; \
	FIGURES_FLAG=""; \
	if [ "$(DESCRIBE_FIGURES)" = "1" ]; then FIGURES_FLAG="--describe-figures"; fi; \
	FIGURE_MODEL_FLAG=""; \
	if [ -n "$(FIGURE_MODEL)" ]; then FIGURE_MODEL_FLAG="--figure-model $(FIGURE_MODEL)"; fi; \
	docker compose run --rm \
		-e TWOMARKDOWN_TELEMETRY_DIR=/app/telemetry \
		-v "$$INPUT_ABS:$$INPUT_ABS" \
		-v "$$OUTPUT_ABS:$$OUTPUT_ABS" \
		$(SERVICE) uv run python -m twomarkdown.cli \
		--input "$$INPUT_ABS" \
		--output "$$OUTPUT_ABS" \
		$$VERBOSE_FLAG $$DRY_RUN_FLAG $$WORKERS_FLAG $$FORCE_FLAG $$OCR_FLAG $$OCR_BACKEND_FLAG $$CHUNKS_FLAG \
		$$FIGURES_FLAG $$FIGURE_MODEL_FLAG

down:
	@echo ":: down: ."
	docker compose down --remove-orphans

stop-ollama:
	@echo ":: stop-ollama: host"
	@python3 scripts/ollama_host.py stop

# ----------------------------- Backend Package Management ----------------------------- #
.PHONY: uv-lock uv-add uv-update uv-remove uv-lock-regenerate uv-sync

# Usage:
#   make uv-add PKG="package[extras]==version"
#   make uv-update
#   make uv-update PKG=foo
#   make uv-remove PKG=foo
uv-lock:
	@echo ":: uv-lock: backend"
	docker compose run --rm $(SERVICE) uv lock

uv-add:
	@echo ":: uv-add: backend"
	docker compose run --rm $(SERVICE) uv add $(PKG)

uv-update:
	@echo ":: uv-update: backend"
ifeq ($(PKG),)
	docker compose run --rm $(SERVICE) uv lock --upgrade
else
	docker compose run --rm $(SERVICE) uv lock --upgrade-package $(PKG)
endif

uv-remove:
	@echo ":: uv-remove: backend"
	docker compose run --rm $(SERVICE) uv remove $(PKG)

uv-lock-regenerate:
	@echo ":: uv-lock-regenerate: backend"
	docker compose run --rm $(SERVICE) uv lock --refresh

uv-sync:
	@echo ":: uv-sync: backend"
	@test -n "$(EXTRA)" || (echo 'Usage: make uv-sync EXTRA=audio-whisper' && exit 1)
	$(call run_uv,sync --extra $(EXTRA) --extra dev)

# ----------------------------- Terminals ----------------------------- #
.PHONY: backend-shell

backend-shell:
	@echo ":: shell: backend"
	docker compose exec $(SERVICE) bash

# ----------------------------- Desktop app (native host) ----------------------------- #
# THE documented exception to "Docker is the transport": the API here needs
# host Ollama, the host GPU, and arbitrary user folders outside any bind
# mount — none of which a container can give it. See docs/README.md's Key
# decisions for why. `uv run` on the host is normally forbidden by this
# Makefile's own rule ("never uv add/sync on the host") but IS allowed for
# this target only, because `uv run` here only needs the venv synced, never
# a lockfile change — first run installs into a native .venv from the
# existing uv.lock, same dependency set the Docker image uses.
.PHONY: app app-web app-build app-down

# Ports `make app` binds: fail fast (before touching either process) instead
# of uvicorn/vite themselves erroring out confusingly (or, worse, silently
# talking to somebody else's already-running server) when a previous `make
# app` was never torn down. Named by PID+command via `lsof` so the message
# is actionable — "which terminal do I go Ctrl-C in" — not just "port busy".
#
# N5: `make app-down para pararlos` is only true when the process holding
# the port is actually one of ours — the exact same test `app-down` itself
# uses to decide what it will kill (a uvicorn whose command names
# `twomarkdown.server` on 8765, a vite dev server whose cwd is this repo's
# `app/` on 5173). A port held by anything else (some unrelated `python3 -m
# http.server`, another app entirely) survives `make app-down` untouched —
# telling the user to run it anyway sends them to a command that will report
# "nothing of ours was running" and leave the port exactly as busy as
# before. Branch the remedy on that same ownership test instead, and keep
# both lines in Spanish (the app's copy is Spanish-only; the old first line
# was English).
define check_port_free
	pid=$$(lsof -ti tcp:$(1) -sTCP:LISTEN 2>/dev/null | head -1); \
	if [ -n "$$pid" ]; then \
		cmd=$$(ps -p $$pid -o command= 2>/dev/null); \
		ours=0; \
		case "$(1)" in \
			8765) echo "$$cmd" | grep -q "twomarkdown.server" && ours=1 ;; \
			5173) cwd=$$(lsof -a -p $$pid -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'); \
				case "$$cwd" in \
					"$(CURDIR)/app"|"$(CURDIR)/app/") ours=1 ;; \
				esac ;; \
		esac; \
		echo ":: app: el puerto $(1) ya está en uso (pid $$pid: $$cmd)"; \
		if [ "$$ours" = "1" ]; then \
			echo ":: app: make app-down para pararlos"; \
		else \
			echo ":: app: lo usa otro programa — páralo tú o cambia de puerto"; \
		fi; \
		exit 1; \
	fi
endef


app-web:
	@echo ":: app-web: app"
	cd app && $(FRONTEND_PM) install && $(FRONTEND_PM) run dev

app-build:
	@echo ":: app-build: app"
	cd app && $(FRONTEND_PM) install && $(FRONTEND_PM) run build

# Runs the FastAPI server natively (background, uv-managed venv) and the Vite
# dev server in the foreground, in one terminal. Ctrl-C (or any exit) tears
# down the API via a trap so a stray uvicorn never survives the terminal.
#
# N10: a bare `kill %1` (SIGTERM only, no follow-up) used to be able to leave
# the uvicorn process — and its `uv run` parent — alive indefinitely: an open
# `WS /api/jobs/{id}/events` connection kept uvicorn's own graceful shutdown
# waiting forever (the port closed, but the python process sat in state S).
# `--timeout-graceful-shutdown 3` and `server/app.py`'s lifespan (which now
# actively closes every open events socket) fix that from the inside, but
# this trap no longer just trusts it: it polls for the pid to actually
# disappear and escalates to SIGKILL — of both the uvicorn pid and its `uv
# run` parent — if it hasn't within ~5s, so Ctrl-C here can never leave an
# orphan pair behind either.
app:
	@echo ":: app: api+web"
	@$(call check_port_free,8765)
	@$(call check_port_free,5173)
	@uv run uvicorn twomarkdown.server:app --host 127.0.0.1 --port 8765 --timeout-graceful-shutdown 3 & \
	UVPID=$$!; \
	trap 'kill $$UVPID 2>/dev/null; n=0; while kill -0 $$UVPID 2>/dev/null && [ $$n -lt 5 ]; do n=$$((n + 1)); sleep 1; done; if kill -0 $$UVPID 2>/dev/null; then echo ":: app: uvicorn (pid $$UVPID) did not exit after 5s, sending SIGKILL"; kill -9 $$UVPID 2>/dev/null; fi; PPID_UV=$$(ps -o ppid= -p $$UVPID 2>/dev/null | tr -d " "); if [ -n "$$PPID_UV" ] && kill -0 $$PPID_UV 2>/dev/null; then echo ":: app: uv run parent (pid $$PPID_UV) still alive, sending SIGKILL"; kill -9 $$PPID_UV 2>/dev/null; fi' EXIT; \
	echo ":: app: waiting for api"; \
	i=0; \
	until curl -s -o /dev/null 127.0.0.1:8765; do \
		i=$$((i + 1)); \
		if [ $$i -ge 20 ]; then \
			echo ":: app: api did not come up after 20s, continuing anyway"; \
			break; \
		fi; \
		sleep 1; \
	done; \
	cd app && $(FRONTEND_PM) install && $(FRONTEND_PM) run dev

# Kills only THIS repo's own `make app` processes: a uvicorn bound to
# twomarkdown.server, and a vite dev server whose cwd is this repo's app/ —
# matched by cwd (via `lsof -d cwd`), not just by command name, so a `make
# app` running from a different checkout of this same repo is left alone.
# `make app` itself never needs this (its own `trap ... EXIT` already tears
# its own two processes down on Ctrl-C) — it's for the case that trap didn't
# run: a killed terminal, a crashed shell, `make app` started in the
# background.
# N10: a plain `kill $pid` only *asks* the process to stop — if it ignores
# that (the whole bug this fixes: an open events WebSocket used to make
# uvicorn's own graceful shutdown wait forever, port closed but the process
# still in state S), `app-down` used to report success and leave it running.
# Each kill below is followed by up to ~5s of polling (`kill -0`, 1s steps)
# and, if the pid is still alive after that, a `kill -9` — of the uvicorn pid
# itself and, since it is a *child* of `uv run` (this target's own
# documented host exception — see the block comment above `app-web`), that
# parent pid too, so neither half of the pair survives.
define wait_then_kill9
	n=0; \
	while kill -0 $(1) 2>/dev/null && [ $$n -lt 5 ]; do n=$$((n + 1)); sleep 1; done; \
	if kill -0 $(1) 2>/dev/null; then \
		echo ":: app-down: $(2) (pid $(1)) did not exit after 5s, sending SIGKILL"; \
		kill -9 $(1) 2>/dev/null || true; \
	fi
endef

app-down:
	@echo ":: app-down: api (8765) + web (5173)"
	@found=0; \
	pid=$$(lsof -ti tcp:8765 -sTCP:LISTEN 2>/dev/null | head -1); \
	if [ -n "$$pid" ] && ps -p $$pid -o command= 2>/dev/null | grep -q "twomarkdown.server"; then \
		ppid=$$(ps -o ppid= -p $$pid 2>/dev/null | tr -d " "); \
		echo ":: app-down: killing uvicorn (pid $$pid)"; \
		kill $$pid 2>/dev/null || true; \
		$(call wait_then_kill9,$$pid,uvicorn); \
		if [ -n "$$ppid" ] && kill -0 $$ppid 2>/dev/null; then \
			echo ":: app-down: uv run parent (pid $$ppid) still alive, sending SIGKILL"; \
			kill -9 $$ppid 2>/dev/null || true; \
		fi; \
		found=1; \
	fi; \
	pid=$$(lsof -ti tcp:5173 -sTCP:LISTEN 2>/dev/null | head -1); \
	if [ -n "$$pid" ]; then \
		cwd=$$(lsof -a -p $$pid -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'); \
		case "$$cwd" in \
			"$(CURDIR)/app"|"$(CURDIR)/app/") \
				echo ":: app-down: killing vite (pid $$pid)"; \
				kill $$pid 2>/dev/null || true; \
				$(call wait_then_kill9,$$pid,vite); \
				found=1 ;; \
			*) \
				echo ":: app-down: port 5173 is in use by pid $$pid but its cwd ($$cwd) is not this repo's app/ — leaving it alone" ;; \
		esac; \
	fi; \
	if [ "$$found" = "0" ]; then echo ":: app-down: nothing of ours was running"; fi

# ----------------------------- Debugging ----------------------------- #
.PHONY: logs

logs:
	@echo ":: logs: backend"
	docker compose logs -f $(SERVICE)

# ----------------------------- Code Formatting ----------------------------- #
.PHONY: format lint-fix lint-backend lint-frontend lint

format:
	@echo ":: format: backend"
	$(call run_uv,run --extra dev ruff format twomarkdown/ tests/)

lint-fix:
	@echo ":: lint-fix: backend"
	$(call run_uv,run --extra dev ruff check --fix twomarkdown/ tests/)

lint-backend:
	@echo ":: lint: backend"
	$(call run_uv,run --extra dev ruff check twomarkdown/ tests/)

# Atomic on purpose per the CI contract, but app/ isn't always installed on a
# dev machine (or a CI job that never set up Node/Bun), so this one degrades
# instead of failing when there is nothing to lint.
lint-frontend:
	@echo ":: lint: frontend"
	@if [ -d app/node_modules ]; then \
		cd app && $(FRONTEND_PM) run --if-present lint; \
	else \
		echo ":: lint: frontend :: skipped (app/node_modules missing)"; \
	fi

lint:
	$(MAKE) lint-backend
	$(MAKE) lint-frontend

# ----------------------------- Testing ----------------------------- #
.PHONY: test-backend test-frontend test test-integration bench

# Usage:
#   make test-backend
#   make test-backend TEST=tests/foo.py
#   make test              (backend + frontend)
test-backend:
	@echo ":: test: backend"
ifeq ($(TEST),)
	$(call run_uv_test,run --extra dev pytest tests/ -m "not integration and not bench" -v)
else
	$(call run_uv_test,run --extra dev pytest $(TEST) -v)
endif

# Same skip-when-absent behavior as lint-frontend, and for the same reason —
# but checked with `node -e` against app/package.json's own `scripts` object
# instead of `$(FRONTEND_PM) run --if-present test`: bun's --if-present only
# suppresses the "script not found" error, it does not stop bun from then
# falling back to executing "test" as a bare shell command — and /bin/test
# is a real binary that exits 1 when called with no arguments, so the
# no-script case failed the same as a real test failure would. "lint" has
# no such collision, so lint-frontend's `--if-present` is unaffected.
test-frontend:
	@echo ":: test: frontend"
	@if [ -d app/node_modules ]; then \
		if node -e "process.exit((require('./app/package.json').scripts||{}).test?0:1)"; then \
			cd app && $(FRONTEND_PM) run test; \
		else \
			echo ":: test: frontend :: skipped (no \"test\" script in app/package.json)"; \
		fi; \
	else \
		echo ":: test: frontend :: skipped (app/node_modules missing)"; \
	fi

test:
	$(MAKE) test-backend
	$(MAKE) test-frontend

test-integration:
	@echo ":: test-integration: backend"
	$(call run_uv,run --extra dev pytest tests/ -m integration -v)

bench:
	@echo ":: bench: backend"
	$(call run_uv,run --extra dev pytest tests/test_bench_methods.py -m bench -v)

# ----------------------------- CI Only ----------------------------- #
.PHONY: sync-ci tesseract-ci

# Native dependency install for GitHub Actions runners — no Compose stack
# there, so this is the CI-only counterpart to `make uv-sync` (host `uv`
# use is otherwise forbidden by this Makefile's own rule; see AGENTS.md).
sync-ci:
	@echo ":: sync-ci: ."
	uv sync --extra dev

# Tesseract + language packs for the integration-test runner.
tesseract-ci:
	@echo ":: tesseract-ci: ."
	sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa

# ----------------------------- ⛔️ DANGER ZONE ⛔️ ----------------------------- #
.PHONY: clean clean-builder

# NUCLEAR: named volumes included. `make build` + `make up` afterwards.
clean:
	@echo ":: clean: ."
	docker compose down --volumes --remove-orphans
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete. Run make build && make up to start again."

clean-builder: clean
	@echo ":: clean-builder: ."
	docker builder prune -f
	@echo "Builder prune complete. Run make build afterwards."

# Export iWork documents to PDF with the Apple apps. Runs on the host, not in
# Docker: only Pages/Keynote/Numbers can read the IWA format, and iCloud bundles
# ship just a first-page preview image.
export-iwork:
	@echo ":: export-iwork: host"
	@test -n "$(INPUT)" || (echo "Usage: make export-iwork INPUT=\"/path/to/folder\"" && exit 1)
	python3 scripts/export_iwork.py "$(INPUT)" $(if $(FORCE),--force,) $(if $(DRY_RUN),--dry-run,)

# ------------------------------ Quality gates ------------------------------ #
.PHONY: validate judge

# Deterministic checks over a converted output folder. No model, no network.
validate:
	@echo ":: validate: backend"
	@test -n "$(INPUT)" || (echo 'Usage: make validate INPUT="/path/to/output_2markdown"' && exit 1)
	@set -e; \
	INPUT_ABS=$$(cd "$(INPUT)" && pwd); \
	docker compose run --rm -v "$$INPUT_ABS:$$INPUT_ABS" $(SERVICE) \
		uv run python -m twomarkdown.validate "$$INPUT_ABS"

# LLM review queue: flags mathematically implausible passages. Writes review-queue.md.
judge:
	@echo ":: judge: backend"
	@test -n "$(INPUT)" || (echo 'Usage: make judge INPUT="/path/to/output_2markdown"' && exit 1)
	@python3 scripts/ollama_host.py ensure
	@set -e; \
	INPUT_ABS=$$(cd "$(INPUT)" && pwd); \
	docker compose run --rm -v "$$INPUT_ABS:$$INPUT_ABS" $(SERVICE) \
		uv run python -m twomarkdown.judge "$$INPUT_ABS" $(if $(JUDGE_MODEL),--model $(JUDGE_MODEL),)
