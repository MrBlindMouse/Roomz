"""Health, library API, and WebSocket control against a fake MPD."""

import asyncio
import os
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.mpd_access import get_mpd
from app.stream import fifo_is_ready


class FakeMpd:
    """In-memory stand-in for the command client."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.state = "stop"
        self.updating = False
        self.added: list[str] = []

    async def snapshot(self) -> dict:
        return {
            "type": "status",
            "state": self.state,
            "is_playing": self.state == "play",
            "elapsed": 0.0,
            "duration": 0.0,
            "song_id": None,
            "title": "",
            "artist": "",
            "file": "",
            "random": False,
            "repeat": 0,
            "server_timestamp": 0.0,
            "playlist": [],
            "stream": {"rate": 44100, "channels": 2, "frame_samples": 882},
        }

    async def play(self) -> None:
        self.calls.append("play")
        self.state = "play"

    async def seek(self, seconds: float) -> None:
        self.calls.append(("seek", seconds))

    async def list_library(self) -> list[dict]:
        return [
            {
                "file": "a/b.mp3",
                "title": "B",
                "artist": "C",
                "album": "",
                "duration_seconds": 1.5,
                "folder": "a",
            }
        ]

    async def is_updating(self) -> bool:
        return self.updating

    async def update_library(self) -> None:
        self.calls.append("update")

    async def add(self, uri: str) -> None:
        self.added.append(uri)


def test_health_is_down_without_mpd(client: TestClient) -> None:
    """No daemon and no FIFO is unhealthy."""
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["mpd"] == "down"
    assert body["fifo"] == "down"


def test_health_ok_when_mpd_and_fifo_are_up(client: TestClient, tmp_path, monkeypatch) -> None:
    """A greeting plus a FIFO node is enough for readiness."""
    ready = threading.Event()
    stop = threading.Event()
    holder: dict = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"OK MPD 0.23.15\n")
        await writer.drain()
        try:
            await reader.read(100)
        finally:
            writer.close()

    def thread_main() -> None:
        async def serve() -> None:
            srv = await asyncio.start_server(handle, "127.0.0.1", 0)
            holder["port"] = srv.sockets[0].getsockname()[1]
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.05)
            srv.close()
            await srv.wait_closed()

        asyncio.run(serve())

    thread = threading.Thread(target=thread_main, daemon=True)
    thread.start()
    assert ready.wait(2)
    fifo = tmp_path / "roomz.fifo"
    os.mkfifo(fifo)
    assert fifo_is_ready(fifo)
    monkeypatch.setenv("MPD_HOST", "127.0.0.1")
    monkeypatch.setenv("MPD_PORT", str(holder["port"]))
    monkeypatch.setenv("ROOMZ_FIFO", str(fifo))
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        stop.set()
        thread.join(timeout=2)


def test_get_root_returns_200(client: TestClient) -> None:
    """SPA root returns 200."""
    response = client.get("/")
    assert response.status_code == 200


def test_library_lists_mpd_tracks(client: TestClient) -> None:
    """GET /api/library returns the fake MPD index."""
    main_app.dependency_overrides[get_mpd] = lambda: FakeMpd()
    try:
        response = client.get("/api/library")
        assert response.status_code == 200
        body = response.json()
        assert body["tracks"][0]["file"] == "a/b.mp3"
        assert "music_directory" in body
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)


def test_scan_conflict_when_mpd_is_updating(client: TestClient) -> None:
    """A second scan while MPD is updating is 409."""
    fake = FakeMpd()
    fake.updating = True
    main_app.dependency_overrides[get_mpd] = lambda: fake
    try:
        response = client.post("/api/scan")
        assert response.status_code == 409
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)


def test_upload_rejects_non_audio(client: TestClient) -> None:
    """Extensions outside the allowlist are rejected before MPD is touched."""
    response = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_writes_into_the_music_directory(client: TestClient, tmp_path, monkeypatch) -> None:
    """Upload saves the file, asks MPD to update, and queues the filename."""
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(tmp_path))
    fake = FakeMpd()
    main_app.dependency_overrides[get_mpd] = lambda: fake
    try:
        response = client.post(
            "/api/upload",
            files={"file": ("song.mp3", b"not-really", "audio/mpeg")},
        )
        assert response.status_code == 200
        assert response.json()["file"] == "song.mp3"
        assert (tmp_path / "song.mp3").is_file()
        assert fake.added == ["song.mp3"]
        assert "update" in fake.calls
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)


def test_uncaught_exception_handler_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The generic handler logs when a dependency raises."""

    def bad_get_mpd():
        raise RuntimeError("test uncaught")

    main_app.dependency_overrides[get_mpd] = bad_get_mpd
    try:
        try:
            client.get("/api/library")
        except Exception:
            pass
        assert any("Uncaught exception" in record.message for record in caplog.records)
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)


def test_websocket_play_and_chat(client: TestClient) -> None:
    """Play is forwarded to MPD and chat is broadcast on the same socket."""
    fake = FakeMpd()
    main_app.state.mpd = fake
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "status"
        assert first["is_playing"] is False
        ws.send_json({"type": "play"})
        playing = ws.receive_json()
        assert playing["is_playing"] is True
        assert fake.calls == ["play"]
        ws.send_json({"type": "seek", "position_seconds": 3.25})
        ws.receive_json()
        assert fake.calls[-1] == ("seek", 3.25)
        ws.send_json({"type": "sync", "client_time": 12.0})
        sync = ws.receive_json()
        assert sync["type"] == "sync_response"
        assert sync["client_time"] == 12.0
        assert isinstance(sync["server_utc"], float)
        ws.send_json({"type": "chat", "author": "Ada", "text": "hi"})
        chat = ws.receive_json()
        assert chat["type"] == "chat"
        assert chat["author"] == "Ada"
        assert chat["text"] == "hi"
