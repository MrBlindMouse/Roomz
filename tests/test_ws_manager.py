"""PCM fanout must not wait on a client that cannot keep up."""

import asyncio
import time

from app.ws_manager import ConnectionManager


class StubSocket:
    """WebSocket stand-in. delay holds send_bytes so a peer looks slow."""

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.sent: list[bytes] = []
        self.texts: list[str] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        await asyncio.sleep(self.delay)
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        self.texts.append(data)

    async def close(self) -> None:
        self.closed = True


async def test_slow_pcm_client_is_dropped_without_blocking_the_others() -> None:
    """A socket stuck in send loses the stream. A fast one still gets every frame."""
    manager = ConnectionManager()
    fast = StubSocket()
    slow = StubSocket(delay=0.5)
    await manager.connect(fast)
    await manager.connect(slow)
    frames = [bytes([n]) for n in range(5)]
    started = time.perf_counter()
    for frame in frames:
        await manager.broadcast_bytes(frame)
    elapsed = time.perf_counter() - started
    for _ in range(5):
        await asyncio.sleep(0)
    assert elapsed < 0.2
    assert fast.sent == frames
    assert slow.closed
    assert slow not in [peer.ws for peer in manager._peers.values()]
    manager.disconnect(fast)


async def test_send_to_delivers_text() -> None:
    """Control messages still arrive on the per-client sender."""
    manager = ConnectionManager()
    socket = StubSocket()
    await manager.connect(socket)
    await manager.send_to(socket, {"type": "sync_response", "client_time": 1})
    assert socket.texts
    assert "sync_response" in socket.texts[0]
    manager.disconnect(socket)
