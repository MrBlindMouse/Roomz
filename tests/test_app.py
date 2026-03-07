"""Smoke and error-handling tests for the Roomz app."""

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app as main_app


def test_health_returns_200(client: TestClient) -> None:
    """GET /health returns 200 when app and DB are up."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_root_returns_200(client: TestClient) -> None:
    """SPA root returns 200."""
    response = client.get("/")
    assert response.status_code == 200


def test_get_api_library_roots_returns_200_and_list(client: TestClient) -> None:
    """GET /api/library-roots returns 200 and JSON array."""
    response = client.get("/api/library-roots")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_http_exception_returns_correct_status(client: TestClient) -> None:
    """HTTPException (e.g. 404) returns correct status and detail."""
    response = client.get("/api/library-roots/999999", follow_redirects=False)
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data


def test_uncaught_exception_handler_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Global exception handler runs and logs when an uncaught exception occurs."""

    async def bad_get_db():
        raise RuntimeError("test uncaught")
        yield  # never reached

    main_app.dependency_overrides[get_db] = bad_get_db
    try:
        try:
            client.get("/api/library-roots")
        except Exception:
            pass  # TestClient may re-raise when exception is in dependency resolution
        assert any("Uncaught exception" in r.message for r in caplog.records)
    finally:
        main_app.dependency_overrides.pop(get_db, None)
