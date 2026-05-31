OLLAMA_MODEL ?= llama3.2-vision:11b
OLLAMA := $(if $(filter ollama,$(MAKECMDGOALS)),1,$(if $(OLLAMA),$(OLLAMA),0))

# Dummy goal so `make build ollama` works (Make has no --flags)
ollama: ; @:

# ------------------------------ Setup ------------------------------ #
.PHONY: fresh-setup build process stop

# Reset config and stop containers. Run once on a new machine.
fresh-setup:
	cp env_template .env
	$(MAKE) stop
	@echo "Ready. Next: make build   OR   make build ollama"

# Build the converter image.
#   make build          -> Tesseract OCR (default, no GPU, no extra downloads)
#   make build ollama   -> also start Ollama and pull llama3.2-vision:11b
build:
	@test -f .env || (echo "Run make fresh-setup first." && exit 1)
	docker compose build
ifeq ($(OLLAMA),1)
	@python3 scripts/set_ocr_mode.py ollama
	docker compose --profile llm up -d ollama
	@echo "Pulling $(OLLAMA_MODEL) (first run may take several minutes)..."
	docker compose --profile llm exec -T ollama ollama pull $(OLLAMA_MODEL)
	@echo "Ollama OCR ready ($(OLLAMA_MODEL))."
else
	@python3 scripts/set_ocr_mode.py tesseract
	@echo "Tesseract OCR ready."
endif

# Convert a file or folder on your machine.
# Usage: make process INPUT="/path/to/file-or-folder"
#
# Output is written next to the input:
#   /docs/reports     -> /docs/reports_2markdown/
#   /docs/report.pdf  -> /docs/report_2markdown/report.md
process:
	@test -f .env || (echo "Run make fresh-setup && make build first." && exit 1)
	@test -n "$(INPUT)" || (echo 'Usage: make process INPUT="/path/to/file-or-folder"' && exit 1)
	@set -e; \
	INPUT_ABS=$$(cd "$$(dirname "$(INPUT)")" && pwd)/$$(basename "$(INPUT)"); \
	test -e "$$INPUT_ABS" || (echo "Not found: $$INPUT_ABS" && exit 1); \
	WORK_DIR=$$(dirname "$$INPUT_ABS"); \
	if grep -q '^LLM_ENABLED=true' .env; then \
		docker compose --profile llm ps ollama 2>/dev/null | grep -qE 'running|Up' || \
			(echo "Ollama is not running. Run: make build ollama" && exit 1); \
		COMPOSE="docker compose --profile llm"; \
		OLLAMA_FLAG="--ollama"; \
	else \
		COMPOSE="docker compose"; \
		OLLAMA_FLAG=""; \
	fi; \
	$$COMPOSE run --rm \
		-v "$$WORK_DIR:$$WORK_DIR" \
		backend-twomarkdown uv run python -m src.cli \
		--input "$$INPUT_ABS" $$OLLAMA_FLAG -v

stop:
	docker compose --profile llm down --remove-orphans

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
.PHONY: show-backend-logs show-ollama-logs

show-backend-logs:
	docker compose logs -f backend-twomarkdown

show-ollama-logs:
	docker compose --profile llm logs -f ollama

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
