"""PCM framer: fixed-size frames and increasing timestamps."""

import asyncio
import os

from app.stream import FRAME_BYTES, FRAME_SECONDS, iter_pcm_frames, pack_pcm_frame, stream_fifo


async def test_iter_pcm_frames_splits_a_byte_string() -> None:
    """A known byte string becomes frames of FRAME_BYTES with increasing stamps."""
    payload = bytes(i % 256 for i in range(FRAME_BYTES * 2))
    pieces = [payload[:1000], payload[1000:]]

    async def read(n: int) -> bytes:
        if not pieces:
            return b""
        part = pieces[0][:n]
        pieces[0] = pieces[0][n:]
        if not pieces[0]:
            pieces.pop(0)
        return part

    clock = iter([10.0, 10.0 + FRAME_SECONDS])
    frames = [item async for item in iter_pcm_frames(read, now=lambda: next(clock))]

    assert len(frames) == 2
    assert frames[0][1] == payload[:FRAME_BYTES]
    assert frames[1][1] == payload[FRAME_BYTES:]
    assert len(frames[0][1]) == FRAME_BYTES
    assert frames[1][0] > frames[0][0]
    assert frames[0][0] == 10.0 - FRAME_SECONDS


def test_pack_pcm_frame_prefixes_timestamp() -> None:
    """The wire frame is an 8-byte little-endian float64 plus the PCM."""
    packed = pack_pcm_frame(1.5, b"\x00\x01")
    assert len(packed) == 10
    assert packed[8:] == b"\x00\x01"


async def test_stream_fifo_broadcasts_full_frames(tmp_path) -> None:
    """Bytes written to a FIFO come out as packed frames."""
    fifo = tmp_path / "audio.fifo"
    os.mkfifo(fifo)
    payload = b"\x11" * FRAME_BYTES + b"\x22" * FRAME_BYTES
    stop = asyncio.Event()
    frames: list[bytes] = []

    async def broadcast(data: bytes) -> None:
        frames.append(data)
        if len(frames) == 2:
            stop.set()

    def write() -> None:
        fd = os.open(fifo, os.O_WRONLY)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    writer = asyncio.create_task(asyncio.to_thread(write))
    await asyncio.wait_for(stream_fifo(fifo, broadcast, stop), timeout=3)
    await writer

    assert frames[0][8:] == b"\x11" * FRAME_BYTES
    assert frames[1][8:] == b"\x22" * FRAME_BYTES
