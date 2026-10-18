# GitHub Actions reference templates

Replace `PROJECT_SLUG` only if workflow needs project-specific env vars.

## .github/workflows/ci.yml

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  ci:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Create .env from template
        run: cp .env_template .env

      - name: Build
        run: make build

      - name: Up
        run: make up

      - name: Lint
        run: make lint

      - name: Test
        run: make test

      - name: Down
        if: always()
        run: make down
```

## .github/workflows/main.yml (optional — main branch builds)

```yaml
name: Main

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Create .env from template
        run: cp .env_template .env

      - name: Build
        run: make build

      - name: Lint
        run: make lint

      - name: Test
        run: make test
```

Add release/deploy steps only as Makefile targets:

```yaml
      - name: Release
        run: make release
```

## Anti-pattern (never do this)

```yaml
# BAD — duplicates commands outside Makefile
- name: Lint backend
  run: docker compose exec -T backend-acme uv run ruff check .

# GOOD — delegates to Makefile
- name: Lint
  run: make lint
```

## CI secrets (production deploys)

For lint/test, `cp .env_template .env` is sufficient (safe dev defaults). For production secrets, inject via GitHub Secrets — see **env-secrets/reference.md** for the pattern.

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      SECRET_KEY: ${{ secrets.SECRET_KEY }}
    steps:
      - uses: actions/checkout@v4
      - run: make release
```

## Backend-only CI (no frontend service)

Same `ci.yml` — the Makefile `lint` and `test` aggregates handle missing frontend targets. Ensure Makefile defines:

```makefile
lint:
	make lint-backend

test:
	make test-backend
```
