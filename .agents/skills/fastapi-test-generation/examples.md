# FastAPI Test Generation — Examples

## Test module with multiple scenarios

```python
import pytest


class TestUsers:
    """Test cases for /api/users/."""

    @pytest.mark.asyncio
    async def test_create_user(self, client):
        """POST /api/users/ — happy path returns 201."""
        payload = {"email": "alice@example.com", "name": "Alice"}

        response = await client.post("/api/users/", json=payload)

        assert response.status_code == 201
        data = response.json()
        expected = {
            "id": data["id"],
            "email": payload["email"],
            "name": payload["name"],
        }
        assert data == expected

    @pytest.mark.asyncio
    async def test_create_duplicate_email(self, client):
        """POST /api/users/ — 409 when email already exists."""
        payload = {"email": "alice@example.com", "name": "Alice"}
        await client.post("/api/users/", json=payload)

        response = await client.post("/api/users/", json=payload)

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_get_not_found(self, client):
        """GET /api/users/999 — 404 when user does not exist."""
        response = await client.get("/api/users/999")

        assert response.status_code == 404
```

## conftest.py (shared fixture at app level)

Place at `backend/app/conftest.py`:

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
