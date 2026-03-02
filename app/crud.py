"""CRUD operations for tracks, playlist, and playback state."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import MUSIC_DIR
from app.models import PlaybackState, PlaylistOrder, Track

# ----- Tracks -----


async def create_track(
    session: AsyncSession,
    filename: str,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> Track:
    """Create a track and return it. filepath is data/music/filename."""
    filepath = str(MUSIC_DIR / filename)
    track = Track(
        filename=filename,
        filepath=filepath,
        title=title,
        artist=artist,
        album=album,
        duration_seconds=duration_seconds,
    )
    session.add(track)
    await session.flush()
    return track


async def get_track_by_id(session: AsyncSession, track_id: int) -> Optional[Track]:
    """Get track by primary key."""
    result = await session.execute(select(Track).where(Track.id == track_id))
    return result.scalar_one_or_none()


async def get_track_by_filename(session: AsyncSession, filename: str) -> Optional[Track]:
    """Get track by filename."""
    result = await session.execute(select(Track).where(Track.filename == filename))
    return result.scalar_one_or_none()


async def list_all_tracks(session: AsyncSession) -> list[Track]:
    """Return all tracks (unordered)."""
    result = await session.execute(select(Track).order_by(Track.id))
    return list(result.scalars().all())


# ----- Playlist -----


async def get_ordered_track_ids(session: AsyncSession) -> list[int]:
    """Return playlist order as list of track_ids."""
    result = await session.execute(select(PlaylistOrder.track_id).order_by(PlaylistOrder.position))
    return list(result.scalars().all())


async def get_playlist_entries_with_tracks(
    session: AsyncSession,
) -> list[tuple[PlaylistOrder, Track]]:
    """Return (PlaylistOrder, Track) for each playlist entry, ordered by position."""
    result = await session.execute(
        select(PlaylistOrder, Track)
        .join(Track, PlaylistOrder.track_id == Track.id)
        .order_by(PlaylistOrder.position)
    )
    return list(result.all())


async def set_playlist_order(session: AsyncSession, order: list[int]) -> None:
    """Replace playlist order with given list of track_ids. Removes missing, adds new at end."""
    # Delete all and re-add in new order
    await session.execute(PlaylistOrder.__table__.delete())
    await session.flush()
    for pos, track_id in enumerate(order):
        session.add(PlaylistOrder(track_id=track_id, position=pos))
    await session.flush()


async def add_track_to_playlist(session: AsyncSession, track_id: int) -> None:
    """Append track to end of playlist."""
    order = await get_ordered_track_ids(session)
    if track_id in order:
        return
    position = len(order)
    session.add(PlaylistOrder(track_id=track_id, position=position))
    await session.flush()


async def remove_track_from_playlist(session: AsyncSession, track_id: int) -> None:
    """Remove all entries for this track_id and renumber positions."""
    result = await session.execute(select(PlaylistOrder).where(PlaylistOrder.track_id == track_id))
    entries = list(result.scalars().all())
    for e in entries:
        await session.delete(e)
    await session.flush()
    # Renumber remaining
    order = await get_ordered_track_ids(session)
    await set_playlist_order(session, order)


async def append_track_to_playlist_and_get_position(session: AsyncSession, track_id: int) -> int:
    """Add track to playlist and return its position (for new uploads)."""
    await add_track_to_playlist(session, track_id)
    order = await get_ordered_track_ids(session)
    return order.index(track_id)


# ----- Playback state (singleton) -----


async def get_playback_state(session: AsyncSession) -> Optional[PlaybackState]:
    """Get the single playback state row, or None if not yet created."""
    result = await session.execute(select(PlaybackState).limit(1))
    return result.scalar_one_or_none()


async def get_or_create_playback_state(session: AsyncSession) -> PlaybackState:
    """Get or create the singleton playback state."""
    state = await get_playback_state(session)
    if state is None:
        state = PlaybackState()
        session.add(state)
        await session.flush()
    return state


async def set_playback_state(
    session: AsyncSession,
    *,
    current_track_id: Optional[int] = None,
    is_playing: Optional[bool] = None,
    position_seconds: Optional[float] = None,
) -> PlaybackState:
    """Update playback state. Only provided fields are updated. Returns updated state."""
    state = await get_or_create_playback_state(session)
    if current_track_id is not None:
        state.current_track_id = current_track_id
    if is_playing is not None:
        state.is_playing = is_playing
    if position_seconds is not None:
        state.position_seconds = max(0.0, position_seconds)
    state.updated_at = datetime.utcnow()
    await session.flush()
    return state
