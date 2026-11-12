"""Paths, MPD connection, and server clock.

Music directory, FIFO, and MPD address come from mpd.conf. Environment variables
override those only when set. Roomz does not start MPD.
"""

import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ponytail: one audio_output block, no nested braces. Upgrade path: a real conf parser.
_OUTPUT_BLOCK = re.compile(r"audio_output\s*\{([^}]*)\}", re.DOTALL)
_QUOTED = re.compile(r'^\s*([A-Za-z0-9_]+)\s+"([^"]*)"', re.MULTILINE)


def mpd_conf_path() -> Path:
    """Config file MPD was started with. Default is mpd/mpd.conf in this repo."""
    raw = os.environ.get("ROOMZ_MPD_CONF")
    if raw:
        return Path(raw).expanduser().resolve()
    return (_PROJECT_ROOT / "mpd" / "mpd.conf").resolve()


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _resolve_conf_path(raw: str, conf: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = conf.parent / path
    return path.resolve()


def _first_fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in _QUOTED.findall(text):
        found.setdefault(key, value)
    return found


def _read_mpd_conf() -> tuple[Path, str | None]:
    conf = mpd_conf_path()
    try:
        return conf, conf.read_text(encoding="utf-8")
    except OSError:
        return conf, None


def mpd_fields_from_conf(text: str) -> tuple[str | None, str | None, str | None, int | None]:
    """music_directory, fifo path, bind address, and port, as written in the file."""
    body = _strip_comments(text)
    top = _first_fields(_OUTPUT_BLOCK.sub("", body))
    fifo = None
    for block in _OUTPUT_BLOCK.findall(body):
        fields = _first_fields(block)
        if fields.get("type") == "fifo" and fields.get("path"):
            fifo = fields["path"]
            break
    port = None
    if top.get("port"):
        try:
            port = int(top["port"])
        except ValueError:
            port = None
    bind = top.get("bind_to_address")
    if bind in (None, "any") or (bind is not None and bind.startswith("/")):
        bind = None
    return top.get("music_directory"), fifo, bind, port


def mpd_paths_from_conf(text: str) -> tuple[str | None, str | None]:
    """music_directory and the fifo audio_output path, still as written in the file."""
    music, fifo, _bind, _port = mpd_fields_from_conf(text)
    return music, fifo


def mpd_config_problems() -> list[str]:
    """Missing or unreadable mpd.conf. Empty means Roomz can take its settings from that file."""
    conf, text = _read_mpd_conf()
    if text is None:
        return [
            f"MPD config not found at {conf}. "
            "Copy mpd/mpd.conf.example to mpd/mpd.conf, or set ROOMZ_MPD_CONF."
        ]
    music, fifo, _bind, _port = mpd_fields_from_conf(text)
    problems: list[str] = []
    if not music:
        problems.append(f"{conf} has no music_directory.")
    if not fifo:
        problems.append(f"{conf} has no fifo audio_output path.")
    return problems


def playlist_directory() -> Path | None:
    """Directory where MPD stores named playlists. None when mpd.conf does not set one."""
    conf, text = _read_mpd_conf()
    if not text:
        return None
    body = _strip_comments(text)
    raw = _first_fields(_OUTPUT_BLOCK.sub("", body)).get("playlist_directory")
    if not raw:
        return None
    return _resolve_conf_path(raw, conf)


def library_base() -> Path:
    """Directory MPD indexes and uploads are written into."""
    raw = os.environ.get("ROOMZ_LIBRARY_BASE")
    if raw:
        return Path(raw).expanduser().resolve()
    conf, text = _read_mpd_conf()
    if text:
        music, _fifo, _bind, _port = mpd_fields_from_conf(text)
        if music:
            return _resolve_conf_path(music, conf)
    return DATA_DIR.resolve()


def mpd_host() -> str:
    """MPD protocol host."""
    raw = os.environ.get("MPD_HOST")
    if raw:
        return raw
    _conf, text = _read_mpd_conf()
    if text:
        _music, _fifo, bind, _port = mpd_fields_from_conf(text)
        if bind:
            return bind
    return "127.0.0.1"


def mpd_port() -> int:
    """MPD protocol port."""
    raw = os.environ.get("MPD_PORT")
    if raw:
        return int(raw)
    _conf, text = _read_mpd_conf()
    if text:
        _music, _fifo, _bind, port = mpd_fields_from_conf(text)
        if port is not None:
            return port
    return 6600


def fifo_path() -> Path:
    """PCM FIFO written by MPD and read by Roomz."""
    raw = os.environ.get("ROOMZ_FIFO")
    if raw:
        return Path(raw).expanduser()
    conf, text = _read_mpd_conf()
    if text:
        _music, fifo, _bind, _port = mpd_fields_from_conf(text)
        if fifo:
            return _resolve_conf_path(fifo, conf)
    return Path("/tmp/roomz-audio.fifo")


def ensure_dirs() -> None:
    """Create the music directory if it does not exist."""
    library_base().mkdir(parents=True, exist_ok=True)


def server_timestamp_utc() -> float:
    """Unix seconds for WebSocket sync and frame stamping."""
    return time.time()


def client_log_level_name() -> str | None:
    """Minimum client log level accepted, or None when ingestion is off."""
    raw = os.environ.get("CLIENT_LOG_LEVEL", "").strip().upper()
    if not raw or raw in {"OFF", "NONE", "DISABLED"}:
        return None
    return raw


def client_log_level() -> int:
    """logging module level for client log filtering."""
    name = client_log_level_name()
    if name is None:
        return logging.CRITICAL + 1
    return getattr(logging, name, logging.WARNING)


def roomz_client_token() -> str | None:
    """Shared secret for WebSocket hello. None means logs need no token."""
    raw = os.environ.get("ROOMZ_CLIENT_TOKEN", "").strip()
    return raw or None
