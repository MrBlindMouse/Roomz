"""Global WebSocket connection manager: connect, disconnect, broadcast."""

import json
import logging
from typing import Any, Union

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Single global manager for all WebSocket connections."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new connection."""
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WS connect; total=%d", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a connection (call on close/error)."""
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WS disconnect; total=%d", len(self._connections))

    async def broadcast(self, message: Union[str, dict[str, Any]]) -> None:
        """Send message to all connected clients. JSON-serializes dict."""
        if isinstance(message, dict):
            message = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning("WS send failed: %s", e)
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_bytes(self, data: bytes) -> None:
        """Send one PCM frame to every client."""
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_bytes(data)
            except Exception as e:
                logger.warning("WS binary send failed: %s", e)
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, message: Union[str, dict[str, Any]]) -> None:
        """Send message to a single client."""
        if isinstance(message, dict):
            message = json.dumps(message)
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.warning("WS send_to failed: %s", e)
            self.disconnect(websocket)


manager = ConnectionManager()
