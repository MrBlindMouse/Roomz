"""Single authoritative playback state. All play/pause/seek/set_track go through the Player."""

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock_sync import server_timestamp_utc
from app.crud import get_or_create_playback_state, set_playback_state

logger = logging.getLogger(__name__)


def compute_sync_tick_broadcast_position_seconds(state: dict[str, Any], ts: float) -> float:
    """Wall-clock playback position at ``ts`` for ``sync_tick`` broadcasts.

    While playing, :meth:`Player.get_state_for_snapshot` holds a stale
    ``position_seconds`` (last command only). Extrapolate with
    ``last_update_server_timestamp`` so clients can pair position with
    ``server_timestamp`` without collapsing extrapolated time.
    When paused, return the frozen position.
    """
    pos = max(0.0, float(state.get("position_seconds") or 0.0))
    if not state.get("is_playing"):
        return pos
    last_ts = float(state.get("last_update_server_timestamp") or 0.0)
    if last_ts <= 0.0:
        last_ts = ts
    return max(0.0, pos + (ts - last_ts))


class Player:
    """Single in-process owner of playback state. Persists via CRUD; returns state dict for broadcast."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current_track_id: Optional[int] = None
        self._position_seconds: float = 0.0
        self._is_playing: bool = False
        self._last_update_server_timestamp: float = 0.0

    async def load_from_session(self, session: AsyncSession) -> None:
        """Load in-memory state from DB (call once at startup)."""
        state = await get_or_create_playback_state(session)
        async with self._lock:
            self._current_track_id = state.current_track_id
            self._position_seconds = max(0.0, state.position_seconds)
            self._is_playing = state.is_playing
            self._last_update_server_timestamp = (
                state.updated_at.timestamp() if state.updated_at else 0.0
            )

    def get_state_for_snapshot(self) -> dict[str, Any]:
        """Return current state dict for state_snapshot (playlist_order must be added by caller)."""
        return {
            "current_track_id": self._current_track_id,
            "is_playing": self._is_playing,
            "position_seconds": self._position_seconds,
            "last_update_server_timestamp": self._last_update_server_timestamp,
        }

    async def apply_command(
        self,
        session: AsyncSession,
        typ: str,
        *,
        track_id: Optional[int] = None,
        position_seconds: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Apply play/pause/seek/set_track. Persist to DB; return broadcast dict or None if unknown typ."""
        ts = server_timestamp_utc()
        async with self._lock:
            if typ == "play":
                await set_playback_state(session, is_playing=True)
                self._is_playing = True
                self._last_update_server_timestamp = ts
                return {
                    "type": "play",
                    "position": self._position_seconds,
                    "server_timestamp": ts,
                    "play_at_server_utc": ts,
                    "track_id": self._current_track_id,
                    "is_playing": True,
                }
            if typ == "pause":
                if position_seconds is not None:
                    pos = max(0.0, position_seconds)
                    await set_playback_state(session, is_playing=False, position_seconds=pos)
                    self._position_seconds = pos
                else:
                    await set_playback_state(session, is_playing=False)
                self._is_playing = False
                self._last_update_server_timestamp = ts
                return {
                    "type": "pause",
                    "position": self._position_seconds,
                    "server_timestamp": ts,
                    "track_id": self._current_track_id,
                    "is_playing": False,
                }
            if typ == "seek":
                pos = position_seconds if position_seconds is not None else 0.0
                pos = max(0.0, pos)
                await set_playback_state(session, position_seconds=pos)
                self._position_seconds = pos
                self._last_update_server_timestamp = ts
                return {
                    "type": "seek",
                    "position": self._position_seconds,
                    "server_timestamp": ts,
                    "track_id": self._current_track_id,
                }
            if typ == "set_track" and track_id is not None:
                await set_playback_state(
                    session,
                    current_track_id=track_id,
                    position_seconds=0.0,
                    is_playing=True,
                )
                self._current_track_id = track_id
                self._position_seconds = 0.0
                self._is_playing = True
                self._last_update_server_timestamp = ts
                return {
                    "type": "set_track",
                    "position": 0,
                    "server_timestamp": ts,
                    "play_at_server_utc": ts,
                    "track_id": track_id,
                    "is_playing": True,
                }
        return None
