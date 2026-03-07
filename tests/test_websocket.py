"""WebSocket tests: connect and receive state_snapshot."""

from fastapi.testclient import TestClient


def test_websocket_connect_receives_state_snapshot(client: TestClient) -> None:
    """Connect to /ws and receive initial state_snapshot message."""
    client.get("/health")  # trigger startup so app.state.player is set
    with client.websocket_connect("/ws") as websocket:
        data = websocket.receive_json()
        assert data.get("type") == "state_snapshot"
        assert "state" in data
        assert "playlist" in data
        state = data["state"]
        assert "current_track_id" in state
        assert "is_playing" in state
        assert "position_seconds" in state
