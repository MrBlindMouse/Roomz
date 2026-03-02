"""Pydantic schemas for API and WebSocket messages."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

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


# ----- WebSocket: client -> server -----


class WsSyncRequest(BaseModel):
    """Client sends for clock sync."""

    type: str = "sync"
    client_time: float  # performance.now() at send


class WsPlay(BaseModel):
    type: str = "play"


class WsPause(BaseModel):
    type: str = "pause"


class WsSeek(BaseModel):
    type: str = "seek"
    position_seconds: float


class WsSetTrack(BaseModel):
    type: str = "set_track"
    track_id: int


class WsChat(BaseModel):
    type: str = "chat"
    text: str
    author: Optional[str] = None


class WsPlaylistReorder(BaseModel):
    type: str = "playlist_reorder"
    order: list[int]


class WsPlaylistAdd(BaseModel):
    type: str = "playlist_add"
    track_id: int


class WsPlaylistRemove(BaseModel):
    type: str = "playlist_remove"
    track_id: int


# ----- WebSocket: server -> client -----


def ws_message(type: str, **payload: Any) -> dict[str, Any]:
    """Build a WebSocket message dict."""
    return {"type": type, **payload}


# state_snapshot: { type, state: PlaybackStateOut, playlist: [PlaylistItem], current_track_filename? }
# sync_response: { type: "sync_response", server_utc, client_time }
# play / pause / seek / set_track: { type, position, server_timestamp, track_id, is_playing? }
# chat: { type: "chat", author, text, timestamp }
# playlist_updated: { type: "playlist_updated", playlist: [PlaylistItem] }
