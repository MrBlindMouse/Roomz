"""Ingest browser and headless client logs over the WebSocket control channel."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

from app.config import client_log_level, client_log_level_name, roomz_client_token

client_logger = logging.getLogger("roomz.client")

_MAX_MESSAGE_LEN = 2000
_MAX_CONTEXT_LEN = 120
_MAX_LABEL_LEN = 80
_MAX_ENTRIES_PER_MESSAGE = 20
_LOGS_PER_MINUTE = 120
_WINDOW_SEC = 60.0

_NAME_TO_LEVEL = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def client_log_ingestion_enabled() -> bool:
    return client_log_level_name() is not None


def client_log_requires_token() -> bool:
    return roomz_client_token() is not None


@dataclass
class ClientLogPeer:
    authenticated: bool = False
    label: str = ""
    window_start: float = field(default_factory=time.monotonic)
    window_count: int = 0


def try_authenticate(peer: ClientLogPeer, token: object, label: object, host: str) -> tuple[bool, str]:
    """Mark a WebSocket peer as allowed to send client logs."""
    expected = roomz_client_token()
    if expected is not None:
        if not isinstance(token, str) or not secrets.compare_digest(token, expected):
            return False, "Invalid client token"
    text = label if isinstance(label, str) else ""
    peer.label = text.strip()[:_MAX_LABEL_LEN] or host
    peer.authenticated = True
    client_logger.info("client hello: %s", peer.label)
    return True, ""


def ingest_client_logs(peer: ClientLogPeer, msg: dict, host: str) -> None:
    """Apply level filter, rate limit, and write to the roomz.client logger."""
    if not client_log_ingestion_enabled():
        return
    if client_log_requires_token() and not peer.authenticated:
        return
    if not peer.authenticated:
        peer.label = host
        peer.authenticated = True

    threshold = client_log_level()
    entries = _normalize_entries(msg)
    if not entries:
        return
    for level, context, message in entries:
        if level < threshold:
            continue
        if not _rate_allow(peer):
            client_logger.warning("client log rate limit exceeded: %s", peer.label or host)
            return
        prefix = f"[{peer.label or host}]"
        if context:
            client_logger.log(level, "%s %s: %s", prefix, context, message)
        else:
            client_logger.log(level, "%s %s", prefix, message)


def _normalize_entries(msg: dict) -> list[tuple[int, str, str]]:
    raw_entries = msg.get("entries")
    if isinstance(raw_entries, list):
        items = raw_entries[:_MAX_ENTRIES_PER_MESSAGE]
    else:
        items = [msg]
    out: list[tuple[int, str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        level_name = item.get("level")
        if not isinstance(level_name, str):
            continue
        level = _NAME_TO_LEVEL.get(level_name.lower())
        if level is None:
            continue
        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        message = message.strip()[:_MAX_MESSAGE_LEN]
        context = item.get("context")
        if isinstance(context, str):
            context = context.strip()[:_MAX_CONTEXT_LEN]
        else:
            context = ""
        out.append((level, context, message))
    return out


def _rate_allow(peer: ClientLogPeer) -> bool:
    now = time.monotonic()
    if now - peer.window_start >= _WINDOW_SEC:
        peer.window_start = now
        peer.window_count = 0
    peer.window_count += 1
    return peer.window_count <= _LOGS_PER_MINUTE
