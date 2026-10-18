# Env Secrets reference templates

Replace `PROJECT_SLUG` with the project slug.

## .envrc

```bash
# Load local secrets (gitignored). Safe to commit — no values here.
dotenv_if_exists .env
```

## .env_template

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=PROJECT_SLUG
DATABASE_URL=postgresql://postgres:postgres@postgres-PROJECT_SLUG:5432/PROJECT_SLUG
SECRET_KEY=change-me-in-production
DEBUG=true
```

Add project-specific keys below as features are built. Every key here must have a safe placeholder — never real secrets.

## .gitignore (canonical)

Use this as the single source of truth. Other skills link here instead of duplicating.

```
# Environment
.env
.env.local
.env.*.local

# Python
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
.eggs/
dist/
build/
.pytest_cache/
.ruff_cache/
.mypy_cache/
htmlcov/
.coverage

# Node
node_modules/
dist/
.next/
.turbo/
out/

# Docker
postgres_data_*/

# IDE
.vscode/
.idea/
*.swp
*.swo
.DS_Store

# Alembic
alembic/versions/*.pyc
```

## CI secret injection

For lint/test CI, safe defaults are enough:

```yaml
- name: Create .env from template
  run: cp .env_template .env
```

For production secrets in CI, inject via GitHub Secrets as workflow env vars — do not commit them:

```yaml
env:
  DATABASE_URL: ${{ secrets.DATABASE_URL }}
  SECRET_KEY: ${{ secrets.SECRET_KEY }}
```

If a Makefile target needs to assemble CI env, add `make ci-env` first (Makefile parity rule from **github-actions**), then call it from the workflow.

## Per-developer overrides (optional, not scaffolded by default)

Developers may add a gitignored `.env.local` and extend `.envrc` manually:

```bash
dotenv_if_exists .env
dotenv_if_exists .env.local
```

Do not scaffold `.env.local` by default — keep the base contract simple.
