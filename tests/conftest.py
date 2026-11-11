"""Pytest fixtures. Lifespan retries MPD/FIFO when env points at nothing."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """HTTP client with MPD port and FIFO aimed at nothing so startup only retries."""
    monkeypatch.setenv("MPD_HOST", "127.0.0.1")
    monkeypatch.setenv("MPD_PORT", "9")
    monkeypatch.setenv("ROOMZ_FIFO", "/tmp/roomz-pytest-no-such-fifo")
    with TestClient(app) as test_client:
        yield test_client
