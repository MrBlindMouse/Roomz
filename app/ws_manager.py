"""Global WebSocket connection manager: connect, disconnect, broadcast.

PCM is queued per client. A slow socket does not stall the FIFO reader or the
other speakers. One sender task per connection keeps text and binary sends
from overlapping on the same WebSocket.
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# ponytail: one queued PCM frame (~20 ms) besides the send in flight. A second
# waiting frame means the socket is behind the house. Upgrade path: a jitter buffer.
_PCM_QUEUED = 1
_TEXT_QUEUED = 8
_SEND_TIMEOUT = 0.05


class _Peer:
    """Outbound queue for one browser."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self.text: deque[tuple[str, asyncio.Future[None] | None]] = deque()
        self.pcm: deque[bytes] = deque()
        self.inflight: asyncio.Future[None] | None = None
        self.wake = asyncio.Event()
        self.failed = False
        self.task = asyncio.create_task(self._run())
        self.task.add_done_callback(_retrieve)

    def enqueue_text(self, message: str, *, wait: bool) -> asyncio.Future[None] | None:
        """Queue a text frame. When wait is set, the returned future resolves after the send."""
        if self.failed or len(self.text) >= _TEXT_QUEUED:
            self.failed = True
            return None
        future: asyncio.Future[None] | None = None
        if wait:
            future = asyncio.get_running_loop().create_future()
        self.text.append((message, future))
        self.wake.set()
        return future

    def enqueue_pcm(self, frame: bytes) -> bool:
        """Queue one PCM frame. False means this client is too far behind."""
        if self.failed or len(self.pcm) >= _PCM_QUEUED:
            self.failed = True
            return False
        self.pcm.append(frame)
        self.wake.set()
        return True

    def fail(self) -> None:
        """Unblock anyone waiting on a text send."""
        self.failed = True
        self.wake.set()
        self._reject(self.inflight)
        self.inflight = None
        while self.text:
            _message, future = self.text.popleft()
            self._reject(future)
        self.pcm.clear()

    @staticmethod
    def _reject(future: asyncio.Future[None] | None) -> None:
        if future is not None and not future.done():
            future.set_exception(ConnectionError("WebSocket closed"))

    async def _run(self) -> None:
        while not self.failed:
            if not self.text and not self.pcm:
                self.wake.clear()
                if self.text or self.pcm:
                    continue
                await self.wake.wait()
                continue
            try:
                if self.text:
                    message, future = self.text.popleft()
                    self.inflight = future
                    await asyncio.wait_for(self.ws.send_text(message), _SEND_TIMEOUT)
                    if future is not None and not future.done():
                        future.set_result(None)
                    self.inflight = None
                else:
                    frame = self.pcm.popleft()
                    await asyncio.wait_for(self.ws.send_bytes(frame), _SEND_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except (ConnectionError, TimeoutError, RuntimeError, OSError) as exc:
                logger.warning("WS send failed: %s", exc)
                self.fail()
                return


def _retrieve(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


class ConnectionManager:
    """Single global manager for all WebSocket connections."""

    def __init__(self) -> None:
        self._peers: dict[WebSocket, _Peer] = {}

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new connection."""
        await websocket.accept()
        self._peers[websocket] = _Peer(websocket)
        logger.info("WS connect; total=%d", len(self._peers))

    def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a connection (call on close/error)."""
        peer = self._peers.pop(websocket, None)
        if peer is None:
            return
        peer.fail()
        peer.task.cancel()
        logger.info("WS disconnect; total=%d", len(self._peers))

    async def broadcast(self, message: str | dict[str, Any]) -> None:
        """Queue a text message for every client. Does not wait on slow sockets."""
        if isinstance(message, dict):
            message = json.dumps(message)
        dead = [
            ws
            for ws, peer in list(self._peers.items())
            if peer.enqueue_text(message, wait=False) is None and peer.failed
        ]
        for ws in dead:
            self._drop(ws)
        await asyncio.sleep(0)

    async def broadcast_bytes(self, data: bytes) -> None:
        """Queue one PCM frame per client. Returns without waiting for sockets."""
        dead = [ws for ws, peer in list(self._peers.items()) if not peer.enqueue_pcm(data)]
        for ws in dead:
            self._drop(ws)
        await asyncio.sleep(0)

    async def send_to(self, websocket: WebSocket, message: str | dict[str, Any]) -> None:
        """Send one text message and wait until that socket accepts it."""
        if isinstance(message, dict):
            message = json.dumps(message)
        peer = self._peers.get(websocket)
        if peer is None:
            return
        future = peer.enqueue_text(message, wait=True)
        if future is None:
            self._drop(websocket)
            return
        try:
            await future
        except (ConnectionError, TimeoutError, RuntimeError, OSError) as exc:
            logger.warning("WS send_to failed: %s", exc)
            self._drop(websocket)

    def _drop(self, websocket: WebSocket) -> None:
        """Forget a client and close it without blocking the caller."""
        self.disconnect(websocket)
        asyncio.create_task(self._close(websocket))

    async def _close(self, websocket: WebSocket) -> None:
        try:
            await websocket.close()
        except (ConnectionError, RuntimeError, OSError) as exc:
            logger.debug("WS close failed: %s", exc)


manager = ConnectionManager()
