# Dockerization reference templates

Replace `PROJECT_SLUG`, `POSTGRES_DB`, and port mappings as needed.

## compose.yaml — FastAPI + React (Vite)

```yaml
services:
  backend-PROJECT_SLUG:
    container_name: PROJECT_SLUG_backend
    build:
      context: .
      dockerfile: docker/backend.Dockerfile
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-postgres}@postgres-PROJECT_SLUG:5432/${POSTGRES_DB:-POSTGRES_DB}
      - UV_PROJECT_ENVIRONMENT=/opt/venv
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app
    depends_on:
      postgres-PROJECT_SLUG:
        condition: service_healthy
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    networks:
      - appnet_PROJECT_SLUG

  postgres-PROJECT_SLUG:
    container_name: PROJECT_SLUG_db
    image: postgres:16-alpine
    build:
      context: .
      dockerfile: docker/postgresql.Dockerfile
    env_file:
      - .env
    environment:
      - POSTGRES_USER=${POSTGRES_USER:-postgres}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-postgres}
      - POSTGRES_DB=${POSTGRES_DB:-POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data_PROJECT_SLUG:/var/lib/postgresql/data
    networks:
      - appnet_PROJECT_SLUG
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-postgres}"]
      interval: 10s
      timeout: 5s
      retries: 5

  frontend-PROJECT_SLUG:
    container_name: PROJECT_SLUG_frontend
    restart: unless-stopped
    build:
      context: .
      dockerfile: docker/frontend.Dockerfile
    command: bun run dev -- --host 0.0.0.0
    volumes:
      - ./frontend:/code
      - /code/node_modules
    ports:
      - "3000:5173"
    depends_on:
      - backend-PROJECT_SLUG
    networks:
      - appnet_PROJECT_SLUG

volumes:
  postgres_data_PROJECT_SLUG:

networks:
  appnet_PROJECT_SLUG:
```

## compose.yaml — DRF + React (Vite)

Same as above except backend `command`:

```yaml
    command: uv run python manage.py runserver 0.0.0.0:8000
```

## compose.yaml — FastAPI + Next

Same as FastAPI + React except frontend ports and command:

```yaml
  frontend-PROJECT_SLUG:
  ...
    command: bun run dev -- --hostname 0.0.0.0
    ports:
      - "3000:3000"
```

## docker/backend.Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/pyproject.toml backend/uv.lock* ./

RUN uv sync --frozen --no-install-project --no-dev \
    || (uv lock && uv sync --no-install-project --no-dev)

COPY backend/ .

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

For DRF, change the `CMD` to:

```dockerfile
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
```

## docker/frontend.Dockerfile — React (Vite)

```dockerfile
FROM oven/bun:1.2.5

WORKDIR /code

COPY frontend/package.json frontend/bun.lock* ./

RUN bun install --frozen-lockfile

COPY frontend/ .

EXPOSE 5173

CMD ["bun", "run", "dev", "--", "--host", "0.0.0.0"]
```

## docker/frontend.Dockerfile — Next

```dockerfile
FROM oven/bun:1.2.5

WORKDIR /code

COPY frontend/package.json frontend/bun.lock* ./

RUN bun install --frozen-lockfile

COPY frontend/ .

EXPOSE 3000

CMD ["bun", "run", "dev", "--", "--hostname", "0.0.0.0"]
```

## docker/postgresql.Dockerfile

```dockerfile
FROM postgres:16-alpine
```

## .env_template

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=PROJECT_SLUG
DATABASE_URL=postgresql://postgres:postgres@postgres-PROJECT_SLUG:5432/PROJECT_SLUG
```
