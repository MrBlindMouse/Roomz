"""Pydantic schemas for the REST API."""

from typing import Optional

from pydantic import BaseModel


class LibraryTrack(BaseModel):
    """One file in the MPD library."""

    file: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_seconds: Optional[float] = None
    folder: Optional[str] = None


class LibraryOut(BaseModel):
    """Library listing. music_directory is the MPD music folder."""

    music_directory: str
    tracks: list[LibraryTrack]


class ScanResult(BaseModel):
    """POST /api/scan finished an MPD database update."""

    ok: bool = True
