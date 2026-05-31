# ------------------------------ Local dev ------------------------------ #
.PHONY: setup convert-local dev clean-out

# First-time: .env + deps + sample files in data/in
setup:
	@test -f .env || cp env_template .env
	uv sync --extra dev
	uv run python scripts/seed_samples.py

# Usage: make convert-local INPUT=data/in
# Optional: OUTPUT=custom/path (default: <INPUT>_2markdown)
convert-local:
	uv run python -m src.cli --input $(INPUT) $(if $(OUTPUT),--output $(OUTPUT),) -v

# Convert bundled samples (data/in -> data/in_2markdown)
dev: clean-out
	uv run python -m src.cli --input data/in -v

clean-out:
	rm -rf data/in_2markdown

# ------------------------------ Docker Compose ------------------------------ #
.PHONY: build start stop

build:
	docker compose build

start:
	docker compose up -d --remove-orphans

stop:
	docker compose down --remove-orphans

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

# ----------------------------- Conversion ----------------------------- #
.PHONY: convert

# Usage: make convert INPUT=/data/in
# Optional: OUTPUT=/data/custom-out
convert:
	docker compose run --rm backend-twomarkdown uv run python -m src.cli \
		--input $(INPUT) $(if $(OUTPUT),--output $(OUTPUT),) -v

# Seed samples inside Docker volume
docker-seed:
	docker compose run --rm backend-twomarkdown uv run python scripts/seed_samples.py

# ----------------------------- Code Formatting ----------------------------- #
.PHONY: lint format

lint:
	docker compose run --rm backend-twomarkdown uv run ruff check src/ tests/

format:
	docker compose run --rm backend-twomarkdown uv run ruff format src/ tests/

# ----------------------------- Testing ----------------------------- #
.PHONY: tests test

tests:
	docker compose run --rm backend-twomarkdown uv run pytest tests/ -m "not integration" -v

test:
	docker compose run --rm backend-twomarkdown uv run pytest $(TEST) -v
