---
name: fastapi-test-generation
description: Generates FastAPI pytest tests with httpx AsyncClient, per-app tests/ directories, and explicit status/JSON assertions. Use when creating tests for FastAPI backends or when the user asks for pytest API tests in the project's conventions.
---

# FastAPI Test Generation

Create **FastAPI + pytest** tests with `httpx.AsyncClient`, placed in each feature package's `tests/` directory.

## Quick start

1. Identify the endpoint under test (method, path, request/response schema).
2. Derive a small scenario set: happy path, auth errors, validation errors, not-found.
3. Write pytest tests using `httpx.AsyncClient` with explicit assertions on:
   - status code
   - response JSON (exact dict or key invariants)
   - database side effects (when applicable)

## Conventions

### Test file location

Tests live inside each feature package:

```
backend/app/<feature>/tests/test_<area>.py
```

Not in a top-level `tests/` folder.

### Test case structure

- Use `pytest` with `httpx.AsyncClient` and `pytest.mark.asyncio`.
- Import the FastAPI app: `from app.main import app`.
- Use a fixture for the async test client.
- One feature-oriented test class or module per file, named `Test<Area>`.

### Client fixture

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

### Naming + docstrings

- Test functions: `test_<action>_<scenario>`.
- Each test should have a docstring describing endpoint behavior:
  - `"""GET /api/items/ — returns empty list when no items exist."""`
  - `"""POST /api/items/ — 422 when name is missing."""`

### Assertions

- Status codes: `assert response.status_code == 200`
- JSON: `data = response.json()` then:
  - Prefer `expected = {...}` and `assert data == expected` when stable.
  - When values are variable (ids, timestamps), assert invariants:
    - key presence: `assert "id" in data`
    - value relations: `assert data["name"] == payload["name"]`
- DB side effects (when using a test database):
  - Query the session directly or use a test DB fixture.
  - Assert create/update/delete side effects.

### Organization for multi-endpoint suites

Group tests with section banners:

```python
# -------------------------------------------------------------------------
# Create
# -------------------------------------------------------------------------
```

## Workflow

1. **Locate entry point**: router function, dependency, or service.
2. **Extract behaviors**:
   - inputs (payload fields, path params, query params)
   - auth requirements
   - validation errors
   - side effects
3. **Choose scenarios** (keep small but high-signal):
   - happy path
   - unauthenticated (401) if protected
   - forbidden (403) when applicable
   - not found (404)
   - invalid payload (422 for Pydantic validation)
4. **Implement**:
   - Arrange with fixtures/factories
   - Act with `await client.<method>(path, json=payload)`
   - Assert status, JSON, and DB changes

## Templates

### Basic endpoint test

```python
import pytest


class TestItems:
    """Test cases for /api/items/."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        """GET /api/items/ — returns empty list when no items exist."""
        response = await client.get("/api/items/")

        assert response.status_code == 200
        assert response.json() == []
```

### Create with payload

```python
    @pytest.mark.asyncio
    async def test_create_item(self, client):
        """POST /api/items/ — creates item and returns 201."""
        payload = {"name": "Widget", "price": 9.99}

        response = await client.post("/api/items/", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == payload["name"]
        assert data["price"] == payload["price"]
```

### Validation error

```python
    @pytest.mark.asyncio
    async def test_create_missing_name(self, client):
        """POST /api/items/ — 422 when name is missing."""
        response = await client.post("/api/items/", json={"price": 9.99})

        assert response.status_code == 422
```

## How to invoke

- "Use the `fastapi-test-generation` skill to create tests for this endpoint."
- "Generate pytest tests for the FastAPI router in `app/items/router.py`."

## Scope limits

- This skill is for **FastAPI + pytest + httpx** tests only.
- For Django/DRF tests, use **django-test-generation**.
- If the URL path/method is not visible in the code, infer from existing patterns and state assumptions in the chat response (not in test code).

## Additional resources

- More examples: [examples.md](examples.md)
