"""Minimal integration test for the Docker Compose stack."""

from __future__ import annotations

import os

import httpx


def test_fastapi_health_endpoint() -> None:
    base_url = os.environ.get("INTEGRATION_BASE_URL", "http://localhost:8000")

    response = httpx.get(f"{base_url}/health", timeout=10)

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
