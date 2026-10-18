---
name: env-secrets
description: Manages environment variables and secrets with direnv (.envrc), .env_template, and gitignored .env. Canonical source for .gitignore patterns. Use when scaffolding env files, setting up secret management, configuring direnv, or adding environment variables to a project.
---

# Env Secrets

Canonical skill for environment variables, secret management, and `.gitignore` patterns in scaffolded projects.

## File contract

| File | Committed? | Purpose |
|------|------------|---------|
| `.envrc` | Yes | direnv entrypoint; loads `.env` when you `cd` into the repo |
| `.env_template` | Yes | Documents every required var with safe placeholder values |
| `.env` | **No** | Real local secrets; created via `cp .env_template .env` |

**`.envrc` does not store secrets** — it is committed and tells direnv how to load them. Secrets live in `.env` (gitignored).

## Standard `.envrc` (repo root only)

```bash
# Load local secrets (gitignored). Safe to commit — no values here.
dotenv_if_exists .env
```

Do not create `.envrc` in `backend/` or `frontend/` subdirs — root-only for this framework.

## direnv setup (human-only)

One-time machine setup:

```bash
brew install direnv   # or: apt install direnv
# add to ~/.zshrc or ~/.bashrc:
eval "$(direnv hook zsh)"   # or bash
```

Per project (after scaffold):

```bash
cp .env_template .env
# edit .env with real local values
direnv allow .
```

## Scaffold workflow

When invoked by **project-scaffold** or directly:

1. **Create** `.envrc` from [reference.md](reference.md) (replace nothing — static template).
2. **Create** `.env_template` with `PROJECT_SLUG` substituted and all required keys.
3. **Create** `.gitignore` from the canonical template in [reference.md](reference.md).
4. **Do not create** `.env` — tell the user to copy from template and run `direnv allow .`.

## Secret layers

| Layer | Source | Notes |
|-------|--------|-------|
| Local dev | `.env` via `.envrc`/direnv | Real secrets; never committed |
| Docker Compose | `env_file: [.env]` at repo root | Same `.env` file Compose reads |
| CI (lint/test) | `cp .env_template .env` | Safe dev defaults; see **github-actions** |
| CI (production) | GitHub Secrets → env vars before `make` | Never written to `.env` in logs |
| Production deploy | Platform secrets (Vercel, Railway, etc.) | Outside repo |

## Rules

1. **Never commit** `.env`, `.env.local`, or `.env.*.local`.
2. **Never put secrets in `.envrc`** — only loader directives.
3. **Never log secrets** in CI output.
4. **Always document** new env vars in `.env_template` when adding features.
5. **Always keep** `.envrc` at repo root — one loader for the whole stack.

## Integration

| Consumer | How it reads env |
|----------|-----------------|
| direnv / shell | `.envrc` → `.env` |
| Docker Compose | `env_file: [.env]` in compose.yaml |
| FastAPI | `pydantic-settings` / `DATABASE_URL` from env |
| DRF | `os.environ` / `python-dotenv` in settings |
| GitHub Actions | `cp .env_template .env` or GitHub Secrets → `env:` block |
| Makefile | Inherits shell env when run locally; CI uses workflow env |

## Additional resources

- File templates (`.envrc`, `.env_template`, `.gitignore`): [reference.md](reference.md)
