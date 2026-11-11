"""Named playlists live in MPD. Roomz only remembers which name is active."""

from pathlib import Path

from app.config import DATA_DIR, playlist_directory
from app.mpd_client import MpdError

_ACTIVE = "active-playlist"


def active_path() -> Path:
    return DATA_DIR / _ACTIVE


def active_name() -> str:
    try:
        return active_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_active_name(name: str) -> None:
    path = active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n", encoding="utf-8")


def clear_active_name() -> None:
    path = active_path()
    try:
        path.unlink()
    except OSError:
        pass


def playlist_name(raw: object) -> str:
    """Stored-playlist name MPD can use as a filename."""
    if not isinstance(raw, str):
        raise MpdError("Playlist name is required")
    name = raw.strip()
    if (
        not name
        or len(name) > 80
        or name in (".", "..")
        or name.startswith(".")
        or any(char in name for char in ("/", "\\", '"', "\n", "\r", "\0"))
    ):
        raise MpdError("Invalid playlist name")
    return name


def folder_uri(raw: object) -> str:
    """Library directory to add. '/' is the music root."""
    if raw in ("", "/", None):
        return "/"
    if not isinstance(raw, str):
        raise MpdError("Invalid folder")
    text = raw.strip().strip("/")
    if not text:
        return "/"
    rel = Path(text)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise MpdError("Invalid folder")
    return rel.as_posix()


def write_empty_playlist(name: str) -> None:
    """MPD refuses to save an empty queue. An empty m3u is a stored playlist."""
    directory = playlist_directory()
    if directory is None:
        raise MpdError("mpd.conf has no playlist_directory")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.m3u").write_text("#EXTM3U\n", encoding="utf-8")
