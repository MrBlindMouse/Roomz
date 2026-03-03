"""CRUD operations for library roots, tracks, playlist, and playback state."""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryRoot, PlaybackState, PlaylistOrder, Track

# ----- Library roots -----


async def list_library_roots(session: AsyncSession) -> list[LibraryRoot]:
    """Return all library roots ordered by id."""
    result = await session.execute(select(LibraryRoot).order_by(LibraryRoot.id))
    return list(result.scalars().all())


async def create_library_root(
    session: AsyncSession,
    path: str,
    name: Optional[str] = None,
) -> LibraryRoot:
    """Create a library root. path must be absolute and validated by caller."""
    root = LibraryRoot(path=path, name=name)
    session.add(root)
    await session.flush()
    return root


async def get_library_root_by_id(session: AsyncSession, root_id: int) -> Optional[LibraryRoot]:
    """Get library root by primary key."""
    result = await session.execute(select(LibraryRoot).where(LibraryRoot.id == root_id))
    return result.scalar_one_or_none()


async def delete_library_root(session: AsyncSession, root_id: int) -> None:
    """Remove a library root. Caller must ensure no tracks reference it or handle orphaning."""
    result = await session.execute(select(LibraryRoot).where(LibraryRoot.id == root_id))
    root = result.scalar_one_or_none()
    if root:
        await session.delete(root)
        await session.flush()


async def get_default_library_root(session: AsyncSession) -> Optional[LibraryRoot]:
    """Return the first library root (e.g. for upload when no root_id given)."""
    roots = await list_library_roots(session)
    return roots[0] if roots else None


# ----- Tracks -----


async def create_track(
    session: AsyncSession,
    filename: str,
    filepath: str,
    library_root_id: Optional[int] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> Track:
    """Create a track. filepath is full path on disk; filename is basename for display."""
    track = Track(
        filename=filename,
        filepath=filepath,
        library_root_id=library_root_id,
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


async def get_track_by_filepath(
    session: AsyncSession, library_root_id: int, filepath: str
) -> Optional[Track]:
    """Get track by library root and full filepath (for scan dedup)."""
    result = await session.execute(
        select(Track).where(Track.library_root_id == library_root_id, Track.filepath == filepath)
    )
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


def playlist_entry_to_item_dict(po: PlaylistOrder, t: Track) -> dict:
    """Build a dict for one playlist entry (shape compatible with schemas.PlaylistItem)."""
    return {
        "id": po.id,
        "track_id": po.track_id,
        "position": po.position,
        "filename": t.filename,
        "title": t.title,
        "artist": t.artist,
        "album": t.album,
        "duration_seconds": t.duration_seconds,
    }


async def get_playlist_item_dicts(session: AsyncSession) -> list[dict]:
    """Return playlist as list of item dicts (compatible with schemas.PlaylistItem)."""
    entries = await get_playlist_entries_with_tracks(session)
    return [playlist_entry_to_item_dict(po, t) for po, t in entries]


async def set_playlist_order(session: AsyncSession, order: list[int]) -> None:
    """Replace playlist order with given list of track_ids. Removes missing, adds new at end."""
    # Delete all and re-add in new order
    await session.execute(PlaylistOrder.__table__.delete())
    await session.flush()
    for pos, track_id in enumerate(order):
        session.add(PlaylistOrder(track_id=track_id, position=pos))
    await session.flush()


async def add_track_to_playlist(
    session: AsyncSession, track_id: int
) -> Optional[tuple[PlaylistOrder, Track]]:
    """Append track to end of playlist. Returns (PlaylistOrder, Track) for the new entry, or None if already in playlist."""
    order = await get_ordered_track_ids(session)
    if track_id in order:
        return None
    position = len(order)
    po = PlaylistOrder(track_id=track_id, position=position)
    session.add(po)
    await session.flush()
    track = await get_track_by_id(session, track_id)
    return (po, track) if track else None


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
