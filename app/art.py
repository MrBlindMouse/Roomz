"""Album art from MPD embeds or folder images under the music directory."""

from __future__ import annotations

from pathlib import Path

from app.config import library_base
from app.mpd_client import MpdClient

_FOLDER_ART = ("folder.jpg", "cover.jpg", "Folder.jpg", "Cover.jpg", "album.jpg", "Album.jpg")


def resolve_library_file(relative: str) -> Path:
    """Absolute path for a track file under the music directory."""
    root = library_base().resolve()
    rel = Path(relative.replace("\\", "/"))
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise ValueError("Invalid file path")
    full = (root / rel).resolve()
    if not full.is_relative_to(root):
        raise ValueError("Invalid file path")
    return full


def folder_art_path(relative: str) -> Path | None:
    """First matching cover image beside the track, if any."""
    try:
        parent = resolve_library_file(relative).parent
    except ValueError:
        return None
    for name in _FOLDER_ART:
        candidate = parent / name
        if candidate.is_file():
            return candidate
    return None


def image_media_type(data: bytes) -> str:
    """Best-effort Content-Type from magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


async def load_album_art(mpd: MpdClient, relative: str) -> tuple[bytes, str] | None:
    """Embedded art via MPD, else a folder cover image."""
    if not relative:
        return None
    embedded = await mpd.fetch_album_art(relative)
    if embedded:
        return embedded, image_media_type(embedded)
    path = folder_art_path(relative)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data, image_media_type(data)
