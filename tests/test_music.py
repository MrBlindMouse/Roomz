"""Tests for GET /music/track/{track_id}: 404 when track missing or file missing."""

from fastapi.testclient import TestClient


def test_music_track_not_found_returns_404(client: TestClient) -> None:
    """GET /music/track/{id} with non-existent track id returns 404."""
    response = client.get("/music/track/999999")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
