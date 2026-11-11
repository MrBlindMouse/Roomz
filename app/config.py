"""Paths, MPD settings, and server clock for sync messages."""

import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project data directory. Default music library when ROOMZ_LIBRARY_BASE is unset.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def library_base() -> Path:
    """Directory MPD indexes and uploads are written into."""
    raw = os.environ.get("ROOMZ_LIBRARY_BASE")
    if raw:
        return Path(raw).expanduser().resolve()
    return DATA_DIR.resolve()


def mpd_host() -> str:
    """MPD protocol host. Roomz talks to MPD on localhost."""
    return os.environ.get("MPD_HOST", "127.0.0.1")


def mpd_port() -> int:
    """MPD protocol port."""
    return int(os.environ.get("MPD_PORT", "6600"))


def fifo_path() -> Path:
    """PCM FIFO written by MPD and read by Roomz. Must match mpd.conf."""
    return Path(os.environ.get("ROOMZ_FIFO", "/tmp/roomz-audio.fifo"))


def ensure_dirs() -> None:
    """Create the music directory if it does not exist."""
    library_base().mkdir(parents=True, exist_ok=True)


def server_timestamp_utc() -> float:
    """Unix seconds for WebSocket sync and frame stamping."""
    return time.time()
