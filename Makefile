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
else
define run_uv
docker compose run --rm $(SERVICE) uv $(1)
endef
endif

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
	@echo "            [NO_OCR=1] [EMIT_CHUNKS=1] [SKIP_IWORK_EXPORT=1]"
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
	@echo "Debugging:"
	@echo "  make logs                     Tail backend logs"
	@echo ""
	@echo "Code quality:"
	@echo "  make format                   ruff format"
	@echo "  make lint-fix                 ruff check --fix"
	@echo "  make lint                     ruff check"
	@echo ""
	@echo "Testing:"
	@echo "  make test                     Unit tests (exclude integration)"
	@echo "  make test TEST=tests/foo.py   Run a specific test path"
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
	cp env_template .env
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
	docker compose run --rm \
		-e TWOMARKDOWN_TELEMETRY_DIR=/app/telemetry \
		-v "$$INPUT_ABS:$$INPUT_ABS" \
		-v "$$OUTPUT_ABS:$$OUTPUT_ABS" \
		$(SERVICE) uv run python -m twomarkdown.cli \
		--input "$$INPUT_ABS" \
		--output "$$OUTPUT_ABS" \
		$$VERBOSE_FLAG $$DRY_RUN_FLAG $$WORKERS_FLAG $$FORCE_FLAG $$OCR_FLAG $$OCR_BACKEND_FLAG $$CHUNKS_FLAG

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

# ----------------------------- Debugging ----------------------------- #
.PHONY: logs

logs:
	@echo ":: logs: backend"
	docker compose logs -f $(SERVICE)

# ----------------------------- Code Formatting ----------------------------- #
.PHONY: format lint-fix lint

format:
	@echo ":: format: backend"
	$(call run_uv,run --extra dev ruff format twomarkdown/ tests/)

lint-fix:
	@echo ":: lint-fix: backend"
	$(call run_uv,run --extra dev ruff check --fix twomarkdown/ tests/)

lint:
	@echo ":: lint: backend"
	$(call run_uv,run --extra dev ruff check twomarkdown/ tests/)

# ----------------------------- Testing ----------------------------- #
.PHONY: test test-integration bench

# Usage:
#   make test
#   make test TEST=tests/foo.py
test:
	@echo ":: test: backend"
ifeq ($(TEST),)
	$(call run_uv,run --extra dev pytest tests/ -m "not integration and not bench" -v)
else
	$(call run_uv,run --extra dev pytest $(TEST) -v)
endif

test-integration:
	@echo ":: test-integration: backend"
	$(call run_uv,run --extra dev pytest tests/ -m integration -v)

bench:
	@echo ":: bench: backend"
	$(call run_uv,run --extra dev pytest tests/test_bench_methods.py -m bench -v)

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
