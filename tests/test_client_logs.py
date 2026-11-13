"""Client log hello auth and ingestion."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.client_logs import ClientLogPeer, ingest_client_logs, try_authenticate
from app.main import app as main_app


def test_try_authenticate_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMZ_CLIENT_TOKEN", "secret")
    peer = ClientLogPeer()
    ok, detail = try_authenticate(peer, "wrong", "pi", "127.0.0.1:1")
    assert not ok
    assert detail == "Invalid client token"
    ok, _ = try_authenticate(peer, "secret", "kitchen", "127.0.0.1:1")
    assert ok
    assert peer.authenticated
    assert peer.label == "kitchen"


def test_ingest_filters_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CLIENT_LOG_LEVEL", "WARNING")
    monkeypatch.delenv("ROOMZ_CLIENT_TOKEN", raising=False)
    peer = ClientLogPeer(authenticated=True, label="test")
    caplog.set_level(logging.DEBUG, logger="roomz.client")
    ingest_client_logs(
        peer,
        {
            "entries": [
                {"level": "info", "message": "hidden"},
                {"level": "warn", "message": "shown"},
            ]
        },
        "127.0.0.1:9",
    )
    assert "hidden" not in caplog.text
    assert "shown" in caplog.text


def test_ingest_ignored_without_auth_when_token_required(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CLIENT_LOG_LEVEL", "INFO")
    monkeypatch.setenv("ROOMZ_CLIENT_TOKEN", "secret")
    peer = ClientLogPeer()
    caplog.set_level(logging.INFO, logger="roomz.client")
    ingest_client_logs(peer, {"level": "error", "message": "nope"}, "127.0.0.1:9")
    assert "nope" not in caplog.text


def test_websocket_client_log_with_hello(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.main import app

    monkeypatch.setenv("MPD_HOST", "127.0.0.1")
    monkeypatch.setenv("MPD_PORT", "9")
    monkeypatch.setenv("ROOMZ_FIFO", "/tmp/roomz-pytest-no-such-fifo")
    monkeypatch.setenv("CLIENT_LOG_LEVEL", "INFO")
    monkeypatch.setenv("ROOMZ_CLIENT_TOKEN", "roomz-test")
    caplog.set_level(logging.INFO, logger="roomz.client")

    from tests.test_app import FakeMpd

    main_app.state.mpd = FakeMpd()

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "status"
            ws.send_json({"type": "client_log", "level": "info", "message": "before hello"})
            ws.send_json({"type": "hello", "token": "roomz-test", "label": "pytest"})
            assert ws.receive_json()["type"] == "hello_ok"
            ws.send_json({"type": "client_log", "level": "info", "message": "after hello"})
    assert "after hello" in caplog.text
    assert "before hello" not in caplog.text
