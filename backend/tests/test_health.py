"""Minimal smoke tests for CI."""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_API_KEY", "sk-admin-test-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-3.5-turbo")
os.environ.setdefault("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB_NAME", "minirag_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("STARTUP_TIMEOUT_SECONDS", "1")
os.environ.setdefault("SERVICE_CHECK_TIMEOUT_SECONDS", "1")

from fastapi.testclient import TestClient

from src.database import get_db
from src.main import app


async def override_db():
    """Avoid opening a database connection for validation-only smoke tests."""
    yield object()


app.dependency_overrides[get_db] = override_db
client = TestClient(app)


def test_health_returns_200() -> None:
    response = client.get("/health")

    assert response.status_code == 200


def test_health_status_is_healthy() -> None:
    response = client.get("/health")

    assert response.json()["status"] == "healthy"


def test_health_includes_services_key() -> None:
    response = client.get("/health")

    assert "services" in response.json()


def test_register_missing_fields_returns_422() -> None:
    response = client.post("/api/auth/register", json={})

    assert response.status_code == 422


def test_projects_without_auth_returns_401() -> None:
    response = client.get("/api/projects")

    assert response.status_code == 401
