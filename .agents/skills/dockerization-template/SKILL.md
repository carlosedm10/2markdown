---
name: dockerization-template
description: Scaffolds local dev Docker setup with compose.yaml at repo root and docker/*.Dockerfile for backend, frontend, and PostgreSQL. Use when dockerizing a project, adding compose.yaml, creating docker/ Dockerfiles, or matching the full-stack dev stack (FastAPI or DRF backend, React or Next + Bun frontend, Postgres).
---

# Dockerization Template

## Layout

```
repo-root/
├── .envrc                 # committed — direnv loader (see env-secrets)
├── .env_template          # committed — documented keys
├── .env                   # gitignored — local secrets
├── compose.yaml
├── docker/
│   ├── backend.Dockerfile
│   ├── frontend.Dockerfile
│   └── postgresql.Dockerfile
├── backend/               # app source (mounted in dev)
├── frontend/
└── Makefile               # pair with makefile-operations skill
```

- **One `compose.yaml` at repo root** — not `docker-compose.yml` in subfolders.
- **All Dockerfiles in `docker/`** — filenames `{service}.Dockerfile`, not nested `docker/backend/Dockerfile`.
- **Build context is always `.`** so COPY paths use `backend/` and `frontend/` from the repo root.

## Naming (replace placeholders)

| Placeholder | Example | Used in |
|-------------|---------|---------|
| `PROJECT_SLUG` | `acme` | service suffix, network, volume |
| `backend-{PROJECT_SLUG}` | `backend-acme` | compose service name |
| `{PROJECT_SLUG}_backend` | `acme_backend` | `container_name` |
| `appnet_{PROJECT_SLUG}` | `appnet_acme` | network |
| `postgres_data_{PROJECT_SLUG}` | `postgres_data_acme` | named volume |

Keep service names stable — the Makefile and `docker compose` commands reference them.

## Scaffold workflow

1. **Collect**: `PROJECT_SLUG`, backend type (`fastapi` | `drf`), frontend type (`react` | `next` | none), Python version, Bun version, Postgres major, host ports (default backend `8000`, frontend `3000`, postgres `5432`).
2. **Create** `docker/backend.Dockerfile`, `docker/frontend.Dockerfile`, `docker/postgresql.Dockerfile` from [reference.md](reference.md); pick the backend/frontend variant.
3. **Create** `compose.yaml` from reference; wire `DATABASE_URL` to the postgres service hostname (`postgres-{PROJECT_SLUG}`).
4. **Ensure** env files exist via **env-secrets** (`.envrc`, `.env_template`, `.gitignore`); Compose reads `env_file: [.env]`.
5. **Add** minimal Makefile targets: `build`, `start`, `stop` (see makefile-operations skill).
6. **Verify**: `docker compose config`, then `make build` or `docker compose up --build -d`.

## Backend variants

### FastAPI (default for most APIs)

- Dev command in compose: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Adjust `app.main:app` to match the project's ASGI entrypoint.
- `DATABASE_URL` uses the compose postgres service name.

### DRF / Django

- Dev command in compose: `uv run python manage.py runserver 0.0.0.0:8000`
- `manage.py` must live under the mounted `backend/` path.

## Frontend variants

### React (Vite + Bun)

- Base: `oven/bun:1` (pin tag, e.g. `oven/bun:1.2.5`).
- Expose **5173**; map host `3000:5173` in compose.
- Dev: `bun run dev -- --host 0.0.0.0`
- Anonymous `/code/node_modules` volume so host mount does not wipe deps.

### Next (Bun)

- Base: `oven/bun:1` (pin tag).
- Expose **3000**; map host `3000:3000` in compose.
- Dev: `bun run dev` (ensure `package.json` binds `0.0.0.0` or pass `--hostname 0.0.0.0`).
- Same anonymous `node_modules` volume pattern.

## Design rules

### Backend (Python + uv)

- Base: `python:3.12-slim` (match `pyproject.toml`).
- Install **uv** in the image; set `UV_PROJECT_ENVIRONMENT=/opt/venv` so the venv lives **outside** `/app` — avoids polluting the host when `./backend:/app` is mounted.
- In compose, repeat `UV_PROJECT_ENVIRONMENT=/opt/venv` under `environment`.
- `DATABASE_URL` points at the **compose service name** for Postgres, not `localhost`.
- Dockerfile: `uv sync --frozen --no-install-project --no-dev` with fallback lock+sync; copy `pyproject.toml` + `uv.lock` before full source.

### PostgreSQL

- Image: `postgres:16-alpine` in compose; optional thin `docker/postgresql.Dockerfile` extending `postgres:16` for custom init later.
- **Healthcheck** on postgres; backend `depends_on` with `condition: service_healthy`.
- Named volume for data; env from `.env` with compose defaults: `${POSTGRES_USER:-postgres}` etc.
- Healthcheck user must match `POSTGRES_USER` if not using default `postgres`.

### Compose cross-cutting

- `env_file: [.env]` on backend and postgres.
- Dedicated bridge network per project (`appnet_{PROJECT_SLUG}`).
- `frontend` `depends_on: [backend]` when frontend exists; backend waits on healthy postgres only.
- Do not put production/nginx/certbot in this template — that is a separate prod compose file.

## Checklist

```
- [ ] compose.yaml at repo root
- [ ] docker/backend.Dockerfile, frontend.Dockerfile, postgresql.Dockerfile
- [ ] build.context: . and dockerfile: docker/<name>.Dockerfile
- [ ] Backend venv at /opt/venv, not under mounted /app
- [ ] Postgres healthcheck + backend depends_on condition
- [ ] Frontend: Bun image, frozen lockfile install, node_modules anonymous volume
- [ ] Correct backend command (uvicorn vs runserver) and frontend port mapping
- [ ] .envrc committed, .env_template committed, .env gitignored (see **env-secrets**)
- [ ] make build / start / stop (or documented compose commands)
```

## Additional resources

- Full file templates (FastAPI, DRF, React, Next): [reference.md](reference.md)
- Env/secrets and `.gitignore`: **env-secrets** skill
- Makefile targets, exec vs run, logs: **makefile-operations** skill
