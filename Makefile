OLLAMA_MODEL ?= moondream
OLLAMA := $(if $(filter ollama,$(MAKECMDGOALS)),1,$(if $(OLLAMA),$(OLLAMA),0))

.DEFAULT_GOAL := help

# Dummy goal so `make build ollama` works (Make has no --flags)
ollama: ; @:

# ------------------------------ Help ------------------------------ #
.PHONY: help

help:
	@echo "2markdown — available targets"
	@echo ""
	@echo "Setup:"
	@echo "  make fresh-setup              Copy env_template to .env and stop stack"
	@echo "  make build                    Build converter image (Tesseract OCR, default)"
	@echo "  make build ollama             Build image, start host Ollama, pull moondream"
	@echo "  make build ollama OLLAMA_MODEL=llava   Pull a different vision model"
	@echo "  make start                    Start backend container (+ host Ollama if enabled)"
	@echo "  make stop                     Stop Docker containers"
	@echo "  make stop-ollama              Stop host Ollama"
	@echo "  make process INPUT=\"/path\" [VERBOSE=1]   Convert (mounts input + output only)"
	@echo ""
	@echo "Backend package management:"
	@echo "  make uv-lock                  Refresh uv.lock"
	@echo "  make uv-add PKG=\"pkg==1.0\"    Add a dependency"
	@echo "  make uv-update                Upgrade all dependencies"
	@echo "  make uv-update PKG=foo        Upgrade one package"
	@echo "  make uv-remove PKG=foo        Remove a dependency"
	@echo "  make uv-lock-regenerate       Regenerate lock file from scratch"
	@echo ""
	@echo "Terminals:"
	@echo "  make backend-shell            Open a shell in the backend container"
	@echo ""
	@echo "Debugging:"
	@echo "  make show-backend-logs        Tail backend logs"
	@echo ""
	@echo "Code quality:"
	@echo "  make lint                     Run ruff check"
	@echo "  make format                   Run ruff format"
	@echo ""
	@echo "Testing:"
	@echo "  make tests                    Run unit tests (exclude integration)"
	@echo "  make test TEST=tests/foo.py   Run a specific test file or path"
	@echo ""
	@echo "Danger zone:"
	@echo "  make clean                    Stop stack and remove local caches"
	@echo "  make clean-all                Remove local images (re-run make build after)"

# ------------------------------ Setup ------------------------------ #
.PHONY: fresh-setup build start process stop stop-ollama clean

# Reset config and stop stack. Run once on a new machine.
fresh-setup:
	cp env_template .env
	$(MAKE) stop
	@echo "Ready. Next: make build   OR   make build ollama (requires Ollama on host)"

# Build the converter image.
#   make build          -> Tesseract OCR (default, no extra downloads)
#   make build ollama   -> also ensure host Ollama and pull moondream (~2 GB RAM)
#   make build ollama OLLAMA_MODEL=llava   -> pull a different vision model
build:
	@test -f .env || (echo "Run make fresh-setup first." && exit 1)
	docker compose build
ifeq ($(OLLAMA),1)
	@python3 scripts/set_ocr_mode.py ollama
	@python3 scripts/ollama_host.py ensure
	@python3 scripts/ollama_host.py pull $(OLLAMA_MODEL)
	@echo "Ollama OCR ready ($(OLLAMA_MODEL))."
else
	@python3 scripts/set_ocr_mode.py tesseract
	@echo "Tesseract OCR ready."
endif

# Start backend container and ensure host Ollama when LLM OCR is enabled.
start:
	@test -f .env || (echo "Run make fresh-setup && make build first." && exit 1)
	docker compose up -d backend-twomarkdown
	@if grep -q '^LLM_ENABLED=true' .env; then \
		python3 scripts/ollama_host.py ensure; \
	fi
	@echo "Stack started."

# Convert a file or folder on your machine.
# Usage: make process INPUT="/path/to/file-or-folder" [VERBOSE=1]
#
# Output is written next to the input:
#   /docs/reports     -> /docs/reports_2markdown/
#   /docs/report.pdf  -> /docs/report_2markdown/
process:
	@test -f .env || (echo "Run make fresh-setup && make build first." && exit 1)
	@test -n "$(INPUT)" || (echo 'Usage: make process INPUT="/path/to/file-or-folder"' && exit 1)
	@set -e; \
	INPUT_ABS=$$(cd "$$(dirname "$(INPUT)")" && pwd)/$$(basename "$(INPUT)"); \
	test -e "$$INPUT_ABS" || (echo "Not found: $$INPUT_ABS" && exit 1); \
	WORK_DIR=$$(dirname "$$INPUT_ABS"); \
	if [ -d "$$INPUT_ABS" ]; then \
		OUTPUT_ABS="$$WORK_DIR/$$(basename "$$INPUT_ABS")_2markdown"; \
	else \
		BASENAME=$$(basename "$$INPUT_ABS"); \
		STEM=$${BASENAME%.*}; \
		OUTPUT_ABS="$$WORK_DIR/$${STEM}_2markdown"; \
	fi; \
	mkdir -p "$$OUTPUT_ABS"; \
	if grep -q '^LLM_ENABLED=true' .env; then \
		python3 scripts/ollama_host.py ensure; \
		OLLAMA_FLAG="--ollama"; \
	else \
		OLLAMA_FLAG=""; \
	fi; \
	VERBOSE_FLAG=""; \
	if [ "$(VERBOSE)" = "1" ]; then VERBOSE_FLAG="-v"; fi; \
	docker compose run --rm \
		-v "$$INPUT_ABS:$$INPUT_ABS" \
		-v "$$OUTPUT_ABS:$$OUTPUT_ABS" \
		backend-twomarkdown uv run python -m src.cli \
		--input "$$INPUT_ABS" \
		--output "$$OUTPUT_ABS" \
		$$OLLAMA_FLAG $$VERBOSE_FLAG

stop:
	docker compose down --remove-orphans

stop-ollama:
	python3 scripts/ollama_host.py stop


# ----------------------------- Backend Package Management ----------------------------- #
.PHONY: uv-lock uv-add uv-update uv-remove uv-lock-regenerate

# Usage:
#   make uv-add PKG="package==version"
#   make uv-update
#   make uv-update PKG=foo
#   make uv-remove PKG=foo
uv-lock:
	docker compose run --rm backend-twomarkdown uv lock

uv-add:
	docker compose run --rm backend-twomarkdown uv add $(PKG)

uv-update:
ifeq ($(PKG),)
	docker compose run --rm backend-twomarkdown uv lock --upgrade
else
	docker compose run --rm backend-twomarkdown uv lock --upgrade-package $(PKG)
endif

uv-remove:
	docker compose run --rm backend-twomarkdown uv remove $(PKG)

uv-lock-regenerate:
	docker compose run --rm backend-twomarkdown uv lock --refresh

# ----------------------------- Terminals ----------------------------- #
.PHONY: backend-shell

backend-shell:
	docker compose exec backend-twomarkdown bash

# ----------------------------- Debugging ----------------------------- #
.PHONY: show-backend-logs

show-backend-logs:
	docker compose logs -f backend-twomarkdown

# ----------------------------- Code Formatting ----------------------------- #
.PHONY: lint format

lint:
	docker compose run --rm backend-twomarkdown uv run --extra dev ruff check src/ tests/

format:
	docker compose run --rm backend-twomarkdown uv run --extra dev ruff format src/ tests/

# ----------------------------- Testing ----------------------------- #
.PHONY: tests test

tests:
	docker compose run --rm backend-twomarkdown uv run --extra dev pytest tests/ -m "not integration" -v

test:
	docker compose run --rm backend-twomarkdown uv run --extra dev pytest $(TEST) -v

# ----------------------------- ⛔️ DANGER ZONE ⛔️ ----------------------------- #
.PHONY: clean-all

# Soft clean: stop stack, drop local Python caches, prune dangling Docker images.
clean:
	$(MAKE) stop
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -exec rm -rf {} + 2>/dev/null || true
	docker image prune -f
	@echo "Clean complete. Run make build to use the converter again."


# Hard clean: removes locally built images.
clean-all:
	@echo "WARNING: Removes rebuilt images. Re-run make build afterward."
	docker compose down --volumes --remove-orphans --rmi local 2>/dev/null || true
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -exec rm -rf {} + 2>/dev/null || true
	docker image prune -f
	@echo "Clean-all complete. Run make build (or make build ollama) to start fresh."
