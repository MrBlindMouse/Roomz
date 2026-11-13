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

from app.client_logs import ClientLogPeer, ingest_client_logs, try_authenticate

logger = logging.getLogger(__name__)

# ponytail: ten queued chunks (~1 s) besides the send in flight. Past that the
# chunk is skipped and the browser asks for it from the 5 s ring. A dead
# socket still fails the send and is dropped.
_PCM_QUEUED = 10
_TEXT_QUEUED = 8
_SEND_TIMEOUT = 0.25


class _Peer:
    """Outbound queue for one browser."""

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self.client_logs = ClientLogPeer()
        self.text: deque[tuple[str, asyncio.Future[None] | None]] = deque()
        self.pcm: deque[bytes] = deque()
        self.codec = "pcm"
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
        """Queue one chunk. False means the peer has failed. A full queue skips it."""
        if self.failed:
            return False
        if len(self.pcm) >= _PCM_QUEUED:
            return True
        self.pcm.append(frame)
        self.wake.set()
        return True

    def enqueue_resend(self, frame: bytes) -> None:
        """Queue a repair chunk even when the live queue is full."""
        if self.failed:
            return
        self.pcm.append(frame)
        self.wake.set()

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
        await self.broadcast_chunk(data, None)

    async def broadcast_chunk(self, pcm: bytes, opus: bytes | None) -> None:
        """Queue one chunk per client, in that client's codec. Does not wait."""
        dead = []
        for ws, peer in list(self._peers.items()):
            frame = opus if peer.codec == "opus" else pcm
            if frame is None or not peer.enqueue_pcm(frame):
                if frame is not None:
                    dead.append(ws)
        for ws in dead:
            self._drop(ws)
        await asyncio.sleep(0)

    def set_codec(self, websocket: WebSocket, codec: str) -> None:
        """Remember which payload this browser asked for."""
        peer = self._peers.get(websocket)
        if peer is not None:
            peer.codec = "opus" if codec == "opus" else "pcm"

    def codec_of(self, websocket: WebSocket) -> str:
        """pcm or opus for this socket."""
        peer = self._peers.get(websocket)
        return peer.codec if peer is not None else "pcm"

    def opus_listeners(self) -> int:
        """How many browsers currently want Opus."""
        return sum(peer.codec == "opus" for peer in self._peers.values())

    @staticmethod
    def _client_host(websocket: WebSocket) -> str:
        client = websocket.client
        if client is None:
            return "unknown"
        return f"{client.host}:{client.port}"

    def authenticate_client(
        self, websocket: WebSocket, token: object, label: object
    ) -> tuple[bool, str]:
        peer = self._peers.get(websocket)
        if peer is None:
            return False, "WebSocket not connected"
        return try_authenticate(peer.client_logs, token, label, self._client_host(websocket))

    def ingest_client_logs(self, websocket: WebSocket, msg: dict) -> None:
        peer = self._peers.get(websocket)
        if peer is None:
            return
        ingest_client_logs(peer.client_logs, msg, self._client_host(websocket))

    async def send_bytes_to(self, websocket: WebSocket, data: bytes) -> None:
        """Queue one binary frame for a single browser."""
        peer = self._peers.get(websocket)
        if peer is None:
            return
        peer.enqueue_resend(data)
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
