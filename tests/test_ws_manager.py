"""PCM fanout must not wait on a client that cannot keep up."""

import asyncio

from app.ws_manager import ConnectionManager


class StubSocket:
    """WebSocket stand-in. gate holds the first sends so the queue can fill."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.sent: list[bytes] = []
        self.texts: list[str] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        if self.gate is not None and not self.gate.is_set():
            await self.gate.wait()
        self.sent.append(data)

    async def send_text(self, data: str) -> None:
        self.texts.append(data)

    async def close(self) -> None:
        self.closed = True


async def _drain() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_full_queue_skips_chunks_without_dropping_the_client() -> None:
    """A socket that falls behind loses chunks and stays connected."""
    manager = ConnectionManager()
    fast = StubSocket()
    gate = asyncio.Event()
    slow = StubSocket(gate=gate)
    await manager.connect(fast)
    await manager.connect(slow)
    frames = [bytes([n]) for n in range(15)]
    for frame in frames:
        await manager.broadcast_chunk(frame, None)
    gate.set()
    await _drain()
    assert fast.sent == frames
    assert not slow.closed
    assert slow in [peer.ws for peer in manager._peers.values()]
    assert 0 < len(slow.sent) < len(frames)
    manager.disconnect(fast)
    manager.disconnect(slow)


async def test_resend_goes_only_to_the_asker() -> None:
    """A repair chunk is unicast."""
    manager = ConnectionManager()
    asker = StubSocket()
    other = StubSocket()
    await manager.connect(asker)
    await manager.connect(other)
    await manager.send_bytes_to(asker, b"only-asker")
    await _drain()
    assert asker.sent == [b"only-asker"]
    assert other.sent == []
    manager.disconnect(asker)
    manager.disconnect(other)


async def test_opus_peer_gets_opus_and_pcm_peer_gets_pcm() -> None:
    """Each browser receives the codec it asked for."""
    manager = ConnectionManager()
    pcm_peer = StubSocket()
    opus_peer = StubSocket()
    await manager.connect(pcm_peer)
    await manager.connect(opus_peer)
    manager.set_codec(opus_peer, "opus")
    await manager.broadcast_chunk(b"pcm-bytes", b"opus-bytes")
    await _drain()
    assert pcm_peer.sent == [b"pcm-bytes"]
    assert opus_peer.sent == [b"opus-bytes"]
    assert manager.opus_listeners() == 1
    manager.disconnect(pcm_peer)
    manager.disconnect(opus_peer)


async def test_send_to_delivers_text() -> None:
    """Control messages still arrive on the per-client sender."""
    manager = ConnectionManager()
    socket = StubSocket()
    await manager.connect(socket)
    await manager.send_to(socket, {"type": "sync_response", "client_time": 1})
    assert socket.texts
    assert "sync_response" in socket.texts[0]
    manager.disconnect(socket)
