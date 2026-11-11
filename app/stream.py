"""Timestamped PCM frames from MPD's FIFO.

MPD writes one realtime PCM stream. Roomz slices it into fixed frames and
stamps each frame with the server time of its first sample. Clients schedule
those stamps; they do not seek a local file.
"""

import asyncio
import logging
import os
import stat
import struct
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2
FRAME_SAMPLES = 882  # 20 ms at 44100 Hz
FRAME_BYTES = FRAME_SAMPLES * CHANNELS * SAMPLE_BYTES
FRAME_SECONDS = FRAME_SAMPLES / RATE

ReadFn = Callable[[int], Awaitable[bytes]]
NowFn = Callable[[], float]
BroadcastFn = Callable[[bytes], Awaitable[None]]


def stream_format() -> dict[str, int]:
    """Format object included in status messages so browsers match the FIFO."""
    return {"rate": RATE, "channels": CHANNELS, "frame_samples": FRAME_SAMPLES}


def pack_pcm_frame(timestamp: float, pcm: bytes) -> bytes:
    """8-byte little-endian float64 timestamp, then s16le interleaved PCM."""
    return struct.pack("<d", timestamp) + pcm


def fifo_is_ready(path: Path) -> bool:
    """True when path exists and is a FIFO. Does not open it (open would block)."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(mode)


async def read_exact(read: ReadFn, size: int) -> bytes | None:
    """Read size bytes, or None when the reader hits EOF mid-frame."""
    buf = bytearray()
    while len(buf) < size:
        chunk = await read(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


async def iter_pcm_frames(read: ReadFn, *, now: NowFn = time.time):
    """Yield (server_timestamp, pcm) for each full frame.

    The timestamp is the wall clock when the frame finished arriving, minus
    one frame. That is the server time of the first sample, because MPD
    writes the FIFO in real time.
    """
    while True:
        pcm = await read_exact(read, FRAME_BYTES)
        if pcm is None:
            return
        yield now() - FRAME_SECONDS, pcm


async def _wait_readable(fd: int, stop: asyncio.Event) -> bool:
    """Wait until fd can be read, or stop is set. False means stop."""
    loop = asyncio.get_running_loop()
    readable: asyncio.Future[None] = loop.create_future()

    def _on_readable() -> None:
        if not readable.done():
            readable.set_result(None)

    loop.add_reader(fd, _on_readable)
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _pending = await asyncio.wait(
            {readable, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        return stop_task not in done and not stop.is_set()
    finally:
        loop.remove_reader(fd)
        stop_task.cancel()
        try:
            await stop_task
        except asyncio.CancelledError:
            pass


async def stream_fifo(path: Path, broadcast: BroadcastFn, stop: asyncio.Event) -> None:
    """Open the FIFO and broadcast packed frames until EOF or stop.

    Opened read-write: on Linux that succeeds with no writer attached, and
    our own write end keeps read() from returning EOF when MPD restarts.
    O_NONBLOCK plus the fd watcher lets shutdown interrupt the wait.
    """
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    buf = bytearray()

    async def read(n: int) -> bytes:
        if stop.is_set():
            return b""
        while len(buf) < n:
            try:
                chunk = os.read(fd, n - len(buf))
            except BlockingIOError:
                if not await _wait_readable(fd, stop):
                    return b""
                continue
            if not chunk:
                return b""
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    try:
        async for timestamp, pcm in iter_pcm_frames(read):
            if stop.is_set():
                return
            await broadcast(pack_pcm_frame(timestamp, pcm))
    finally:
        os.close(fd)


async def supervise_fifo(path: Path, broadcast: BroadcastFn, stop: asyncio.Event) -> None:
    """Re-open the FIFO until stop. Missing FIFO waits instead of failing the app."""
    while not stop.is_set():
        if not fifo_is_ready(path):
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            logger.info("reading PCM fifo %s", path)
            await stream_fifo(path, broadcast, stop)
            logger.info("PCM fifo closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PCM fifo ended")
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
