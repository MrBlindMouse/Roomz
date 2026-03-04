"""Pytest fixtures for Roomz API and app tests."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """HTTP client for the FastAPI app (no live server)."""
    return TestClient(app)
