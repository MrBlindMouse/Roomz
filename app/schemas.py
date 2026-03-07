"""Pydantic schemas for API and WebSocket messages."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ----- Library roots -----


class LibraryRootOut(BaseModel):
    """Library root as returned by API."""

    id: int
    path: str
    name: Optional[str] = None
    created_at: Optional[datetime] = None


class LibraryRootCreate(BaseModel):
    """Body for POST /api/library-roots."""

    path: str
    name: Optional[str] = None


# ----- Upload / scan -----


class TrackOut(BaseModel):
    """Track metadata returned after upload or in playlist."""

    id: int
    filename: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[datetime] = None


class TrackWithRootOut(TrackOut):
    """Track with library root info for Library browse."""

    library_root_id: Optional[int] = None
    library_root_name: Optional[str] = None
    folder: Optional[str] = None  # parent dir relative to library root; "." when at root


class ScanResult(BaseModel):
    """Result of POST /api/scan."""

    added: int


# ----- Playlist -----


class PlaylistItem(BaseModel):
    """Single item in playlist (track + order info)."""

    id: int
    track_id: int
    position: int
    filename: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_seconds: Optional[float] = None
    folder: Optional[str] = None  # containing folder relative to library root


class PlaylistReorder(BaseModel):
    """Body for PUT /api/playlist/reorder."""

    order: list[int]  # list of track_ids in desired order


class PlaylistAddItem(BaseModel):
    """Body for POST /api/playlist/items."""

    track_id: int


# ----- Playback state (server-authoritative) -----


class PlaybackStateOut(BaseModel):
    """Current playback state for API and WebSocket state_snapshot."""

    current_track_id: Optional[int] = None
    is_playing: bool = False
    position_seconds: float = 0.0
    last_update_server_timestamp: float = 0.0  # UTC seconds
    playlist_order: list[int] = Field(default_factory=list)  # ordered track_ids


# ----- WebSocket (documentation; handler in main.py uses raw JSON) -----
# state_snapshot: { type, state: PlaybackStateOut, playlist: [PlaylistItem], current_track_filename? }
# sync_response: { type: "sync_response", server_utc, client_time }
# play / pause / seek / set_track: { type, position, server_timestamp, track_id, is_playing? }
# chat: { type: "chat", author, text, timestamp }
# playlist_updated: { type: "playlist_updated", playlist: [PlaylistItem] }
