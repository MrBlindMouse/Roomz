"""Timestamped PCM chunks from MPD's FIFO.

MPD writes one realtime PCM stream. Roomz slices it into 100 ms chunks on a
monotonic media clock. A burst already sitting in the pipe does not collapse
those stamps. Browsers chain the chunks; they do not schedule each one from
the wall clock.
"""

import array
import asyncio
import logging
import os
import stat
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2
FRAME_SAMPLES = 4410  # 100 ms at 44100 Hz
FRAME_BYTES = FRAME_SAMPLES * CHANNELS * SAMPLE_BYTES
FRAME_SECONDS = FRAME_SAMPLES / RATE
HEADER = struct.Struct("<IId")
HEADER_BYTES = HEADER.size
HISTORY_CHUNKS = 50  # 5 s
STALL_SECONDS = 0.5
OPUS_RATE = 48000
OPUS_FRAME_SAMPLES = 960  # 20 ms at 48000 Hz
OPUS_FRAMES = 5
PCM_20MS_SAMPLES = 882
OPUS_BITRATE = 64000

ReadFn = Callable[[int], Awaitable[bytes]]
NowFn = Callable[[], float]
WantOpusFn = Callable[[], bool]


@dataclass
class Chunk:
    """One 100 ms slice. Opus bytes are present only while a browser asked for them."""

    epoch: int
    seq: int
    media_ts: float
    pcm: bytes
    opus: bytes | None


BroadcastFn = Callable[[Chunk], Awaitable[None]]

_opus_ok: bool | None = None


def opus_available() -> bool:
    """True when libopus can encode. Cached, including the failure."""
    global _opus_ok
    if _opus_ok is None:
        try:
            _new_encoder()
            _opus_ok = True
        except Exception:
            logger.info("Opus disabled; libopus is not available")
            _opus_ok = False
    return _opus_ok


def stream_format() -> dict[str, object]:
    """Format object included in status messages so browsers match the FIFO."""
    codecs = ["pcm", "opus"] if opus_available() else ["pcm"]
    return {
        "rate": RATE,
        "channels": CHANNELS,
        "frame_samples": FRAME_SAMPLES,
        "codecs": codecs,
    }


def pack_frame(epoch: int, seq: int, media_ts: float, payload: bytes) -> bytes:
    """16-byte header (epoch, sequence, media time) plus the codec payload."""
    return HEADER.pack(epoch & 0xFFFFFFFF, seq & 0xFFFFFFFF, media_ts) + payload


def fifo_is_ready(path: Path) -> bool:
    """True when path exists and is a FIFO. Does not open it (open would block)."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(mode)


class PlayWatch:
    """Detects a seek: elapsed jumps while the same song keeps playing."""

    def __init__(self) -> None:
        self.song_id: int | None = None
        self.elapsed = 0.0
        self.at = 0.0
        self.state = "stop"
        self.seen = False

    def observe(self, song_id: int | None, elapsed: float, state: str, at: float) -> bool:
        """Record one status sample. True when this sample is a seek."""
        seek = False
        if (
            self.seen
            and self.state == "play"
            and state == "play"
            and song_id is not None
            and song_id == self.song_id
            and abs(elapsed - (self.elapsed + (at - self.at))) > STALL_SECONDS
        ):
            seek = True
        self.song_id = song_id
        self.elapsed = elapsed
        self.at = at
        self.state = state
        self.seen = True
        return seek


class StreamClock:
    """Epoch, monotonic media time, and the last five seconds of chunks."""

    def __init__(self, now: NowFn = time.time) -> None:
        self._now = now
        self.epoch = 0
        self._seq = 0
        self._origin: float | None = None
        self._ring: deque[Chunk] = deque(maxlen=HISTORY_CHUNKS)
        self._want_opus = False
        self._encoder: Any = None

    def want_opus(self, on: bool) -> None:
        """Encode the next chunks when any browser asked for Opus."""
        if on and not opus_available():
            on = False
        if self._want_opus and not on:
            self._encoder = None
        self._want_opus = on

    def bump(self) -> None:
        """Start a new timeline. Drops the ring and the encoder state."""
        self.epoch = ((self.epoch + 1) & 0xFFFFFFFF) or 1
        self._seq = 0
        self._origin = None
        self._ring.clear()
        self._encoder = None

    def observe_wait(self, seconds: float) -> bool:
        """A long blocked read is a pause or an underrun. True when the epoch changed."""
        if seconds > STALL_SECONDS:
            self.bump()
            return True
        return False

    def push(self, pcm: bytes) -> Chunk:
        """Stamp one chunk. Later chunks are exactly 100 ms after the epoch origin."""
        if self._origin is None:
            self._origin = self._now() - FRAME_SECONDS
        seq = self._seq
        media_ts = self._origin + seq * FRAME_SECONDS
        opus = self._maybe_encode(pcm)
        chunk = Chunk(self.epoch, seq, media_ts, pcm, opus)
        self._ring.append(chunk)
        self._seq += 1
        return chunk

    def wire(self, epoch: int, seq: int, codec: str) -> bytes | None:
        """Packed frame for a resend, or None when that chunk has left the ring."""
        if epoch != self.epoch:
            return None
        for chunk in self._ring:
            if chunk.seq != seq:
                continue
            if codec == "opus":
                if chunk.opus is None:
                    return None
                return pack_frame(chunk.epoch, chunk.seq, chunk.media_ts, chunk.opus)
            return pack_frame(chunk.epoch, chunk.seq, chunk.media_ts, chunk.pcm)
        return None

    def _maybe_encode(self, pcm: bytes) -> bytes | None:
        if not self._want_opus:
            return None
        try:
            if self._encoder is None:
                self._encoder = _new_encoder()
            return _encode_opus(self._encoder, pcm)
        except Exception:
            logger.exception("Opus encode failed")
            self._encoder = None
            self._want_opus = False
            return None


def _new_encoder() -> Any:
    import opuslib

    encoder = opuslib.Encoder(OPUS_RATE, CHANNELS, opuslib.APPLICATION_AUDIO)
    encoder.bitrate = OPUS_BITRATE
    return encoder


def _resample_20ms(pcm: bytes) -> bytes:
    """882 stereo frames at 44100 Hz become 960 frames at 48000 Hz."""
    src = array.array("h")
    src.frombytes(pcm)
    out = array.array("h", (0,)) * (OPUS_FRAME_SAMPLES * CHANNELS)
    last = PCM_20MS_SAMPLES - 1
    scale = last / (OPUS_FRAME_SAMPLES - 1)
    for index in range(OPUS_FRAME_SAMPLES):
        pos = index * scale
        left = int(pos)
        frac = pos - left
        right = left + 1 if left < last else left
        for channel in range(CHANNELS):
            a = src[left * CHANNELS + channel]
            b = src[right * CHANNELS + channel]
            out[index * CHANNELS + channel] = int(a + (b - a) * frac)
    return out.tobytes()


def _encode_opus(encoder: Any, pcm: bytes) -> bytes:
    """Five 20 ms Opus packets, each prefixed with a little-endian uint16 length."""
    parts: list[bytes] = []
    frame_bytes = PCM_20MS_SAMPLES * CHANNELS * SAMPLE_BYTES
    for index in range(OPUS_FRAMES):
        piece = pcm[index * frame_bytes : (index + 1) * frame_bytes]
        packet = encoder.encode(_resample_20ms(piece), OPUS_FRAME_SAMPLES)
        parts.append(struct.pack("<H", len(packet)) + packet)
    return b"".join(parts)


def decode_opus_payload(payload: bytes) -> bytes:
    """Decode a chunk payload back to 48000 Hz s16le. Used by tests."""
    import opuslib

    decoder = opuslib.Decoder(OPUS_RATE, CHANNELS)
    out = bytearray()
    offset = 0
    packets = 0
    while offset + 2 <= len(payload) and packets < OPUS_FRAMES:
        length = struct.unpack_from("<H", payload, offset)[0]
        offset += 2
        packet = payload[offset : offset + length]
        offset += length
        out.extend(decoder.decode(packet, OPUS_FRAME_SAMPLES))
        packets += 1
    return bytes(out)


def opus_packet_count(payload: bytes) -> int:
    """How many length-prefixed Opus packets are in a chunk payload."""
    offset = 0
    count = 0
    while offset + 2 <= len(payload) and count < OPUS_FRAMES:
        length = struct.unpack_from("<H", payload, offset)[0]
        offset += 2 + length
        count += 1
    return count


async def read_exact(read: ReadFn, size: int) -> bytes | None:
    """Read size bytes, or None when the reader hits EOF mid-frame."""
    buf = bytearray()
    while len(buf) < size:
        chunk = await read(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


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


async def stream_fifo(
    path: Path,
    clock: StreamClock,
    broadcast: BroadcastFn,
    stop: asyncio.Event,
    want_opus: WantOpusFn = lambda: False,
) -> None:
    """Open the FIFO and broadcast packed chunks until EOF or stop.

    Opened read-write: on Linux that succeeds with no writer attached, and
    our own write end keeps read() from returning EOF when MPD restarts.
    O_NONBLOCK plus the fd watcher lets shutdown interrupt the wait.
    Each open is a new epoch. A read that blocks for more than half a second
    is another epoch: MPD paused, or the pipe underran.
    """
    clock.bump()
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    buf = bytearray()

    async def read(n: int) -> bytes:
        if stop.is_set():
            return b""
        while len(buf) < n:
            try:
                chunk = os.read(fd, n - len(buf))
            except BlockingIOError:
                started = time.monotonic()
                if not await _wait_readable(fd, stop):
                    return b""
                if clock.observe_wait(time.monotonic() - started):
                    buf.clear()
                continue
            if not chunk:
                return b""
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    try:
        while not stop.is_set():
            pcm = await read_exact(read, FRAME_BYTES)
            if pcm is None or stop.is_set():
                return
            clock.want_opus(bool(want_opus()))
            await broadcast(clock.push(pcm))
    finally:
        os.close(fd)


async def supervise_fifo(
    path: Path,
    clock: StreamClock,
    broadcast: BroadcastFn,
    stop: asyncio.Event,
    want_opus: WantOpusFn = lambda: False,
) -> None:
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
            await stream_fifo(path, clock, broadcast, stop, want_opus)
            logger.info("PCM fifo closed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PCM fifo ended")
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
