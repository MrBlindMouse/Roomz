"""PCM framer: 100 ms chunks, monotonic stamps, and a five-second ring."""

import asyncio
import os
import struct

import pytest

from app.stream import (
    FRAME_BYTES,
    FRAME_SECONDS,
    HEADER,
    HISTORY_CHUNKS,
    Chunk,
    PlayWatch,
    StreamClock,
    decode_opus_payload,
    opus_available,
    opus_packet_count,
    pack_frame,
    stream_fifo,
)


def test_burst_reads_keep_100ms_spacing() -> None:
    """Chunks drained in one instant are still stamped 100 ms apart."""
    clock = StreamClock(now=lambda: 10.0)
    clock.bump()
    first = clock.push(b"\x00" * FRAME_BYTES)
    second = clock.push(b"\x00" * FRAME_BYTES)
    assert first.seq == 0
    assert second.seq == 1
    assert first.media_ts == pytest.approx(10.0 - FRAME_SECONDS)
    assert second.media_ts == pytest.approx(first.media_ts + FRAME_SECONDS)
    assert first.epoch == second.epoch


def test_long_stall_bumps_the_epoch_and_drops_the_ring() -> None:
    """A blocked read longer than half a second starts a new timeline."""
    clock = StreamClock(now=lambda: 20.0)
    clock.bump()
    first = clock.push(b"\x11" * FRAME_BYTES)
    assert clock.observe_wait(0.1) is False
    assert clock.epoch == first.epoch
    assert clock.observe_wait(0.6) is True
    assert clock.epoch != first.epoch
    nxt = clock.push(b"\x22" * FRAME_BYTES)
    assert nxt.seq == 0
    assert nxt.media_ts == pytest.approx(20.0 - FRAME_SECONDS)
    assert clock.wire(first.epoch, first.seq, "pcm") is None
    packed = clock.wire(nxt.epoch, nxt.seq, "pcm")
    assert packed is not None
    assert packed[HEADER.size :] == b"\x22" * FRAME_BYTES


def test_ring_forgets_chunks_older_than_five_seconds() -> None:
    """The history keeps 50 chunks and nothing from a previous epoch."""
    clock = StreamClock(now=lambda: 5.0)
    clock.bump()
    first: Chunk | None = None
    last: Chunk | None = None
    for _ in range(HISTORY_CHUNKS + 1):
        last = clock.push(b"\x00" * FRAME_BYTES)
        if first is None:
            first = last
    assert first is not None and last is not None
    assert clock.wire(first.epoch, first.seq, "pcm") is None
    assert clock.wire(last.epoch, last.seq, "pcm") is not None
    epoch = clock.epoch
    clock.bump()
    assert clock.wire(epoch, last.seq, "pcm") is None


def test_pack_frame_prefixes_epoch_sequence_and_time() -> None:
    """The wire frame is a 16-byte little-endian header plus the payload."""
    packed = pack_frame(3, 4, 1.5, b"\x00\x01")
    assert len(packed) == HEADER.size + 2
    epoch, seq, media_ts = struct.unpack_from("<IId", packed)
    assert (epoch, seq) == (3, 4)
    assert media_ts == pytest.approx(1.5)
    assert packed[HEADER.size :] == b"\x00\x01"


def test_same_song_elapsed_jump_is_a_seek() -> None:
    """A seek bumps the timeline. A track change and a pause do not."""
    watch = PlayWatch()
    assert watch.observe(1, 1.0, "play", 100.0) is False
    assert watch.observe(1, 1.2, "play", 100.2) is False
    assert watch.observe(1, 10.0, "play", 100.4) is True
    assert watch.observe(2, 0.0, "play", 110.0) is False
    assert watch.observe(2, 0.1, "pause", 111.0) is False
    assert watch.observe(2, 0.1, "play", 120.0) is False
    assert watch.observe(2, 0.3, "play", 120.2) is False


def test_opus_chunk_roundtrip() -> None:
    """One chunk is five Opus packets and decodes back to about 100 ms."""
    if not opus_available():
        pytest.skip("libopus is not available")
    clock = StreamClock(now=lambda: 1.0)
    clock.bump()
    clock.want_opus(True)
    chunk = clock.push(b"\x00" * FRAME_BYTES)
    assert chunk.opus is not None
    assert opus_packet_count(chunk.opus) == 5
    pcm = decode_opus_payload(chunk.opus)
    duration = len(pcm) / (48000 * 2 * 2)
    assert duration == pytest.approx(0.1, abs=0.01)


async def test_stream_fifo_broadcasts_full_frames(tmp_path) -> None:
    """Bytes written to a FIFO come out as packed frames with even spacing."""
    fifo = tmp_path / "audio.fifo"
    os.mkfifo(fifo)
    payload = b"\x11" * FRAME_BYTES + b"\x22" * FRAME_BYTES
    stop = asyncio.Event()
    frames: list[bytes] = []

    async def broadcast(chunk: Chunk) -> None:
        frames.append(pack_frame(chunk.epoch, chunk.seq, chunk.media_ts, chunk.pcm))
        if len(frames) == 2:
            stop.set()

    def write() -> None:
        fd = os.open(fifo, os.O_WRONLY)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    writer = asyncio.create_task(asyncio.to_thread(write))
    await asyncio.wait_for(stream_fifo(fifo, StreamClock(), broadcast, stop), timeout=3)
    await writer

    assert frames[0][HEADER.size :] == b"\x11" * FRAME_BYTES
    assert frames[1][HEADER.size :] == b"\x22" * FRAME_BYTES
    _epoch0, seq0, ts0 = struct.unpack_from("<IId", frames[0])
    _epoch1, seq1, ts1 = struct.unpack_from("<IId", frames[1])
    assert seq1 == seq0 + 1
    assert ts1 == pytest.approx(ts0 + FRAME_SECONDS)
