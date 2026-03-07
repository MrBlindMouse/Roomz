"""API tests: library roots CRUD with real path, playlist endpoints."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import LIBRARY_BASE


def test_post_library_root_returns_200_and_root(client: TestClient) -> None:
    """POST /api/library-roots with path under LIBRARY_BASE returns 200 and the root."""
    with tempfile.TemporaryDirectory(dir=LIBRARY_BASE, prefix="roomz_test_") as tmp:
        path = str(Path(tmp).resolve())
        response = client.post("/api/library-roots", json={"path": path, "name": "Test Root"})
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == path
        assert data["name"] == "Test Root"
        assert "id" in data


def test_post_library_root_duplicate_returns_409(client: TestClient) -> None:
    """POST /api/library-roots with same path twice returns 409."""
    with tempfile.TemporaryDirectory(dir=LIBRARY_BASE, prefix="roomz_test_") as tmp:
        path = str(Path(tmp).resolve())
        client.post("/api/library-roots", json={"path": path})
        response = client.post("/api/library-roots", json={"path": path})
        assert response.status_code == 409


def test_get_playlist_returns_200_and_list(client: TestClient) -> None:
    """GET /api/playlist returns 200 and a list of playlist items."""
    response = client.get("/api/playlist")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_add_playlist_item_404_when_track_missing(client: TestClient) -> None:
    """POST /api/playlist/items with non-existent track_id returns 404."""
    response = client.post("/api/playlist/items", json={"track_id": 999999})
    assert response.status_code == 404
