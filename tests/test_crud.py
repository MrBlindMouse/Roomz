"""Integration tests for CRUD: library roots, tracks, playlist, playback state."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import (
    add_track_to_playlist,
    create_library_root,
    create_track,
    delete_library_root,
    get_default_library_root,
    get_library_root_by_id,
    get_ordered_track_ids,
    get_or_create_playback_state,
    get_playlist_item_dicts,
    get_track_by_id,
    list_library_roots,
    list_all_tracks,
    remove_track_from_playlist,
    set_playback_state,
    set_playlist_order,
)


@pytest.mark.asyncio
async def test_create_and_list_library_roots(db_session: AsyncSession) -> None:
    """Create library roots and list them."""
    root1 = await create_library_root(db_session, "/path/a", "Root A")
    await db_session.flush()
    root2 = await create_library_root(db_session, "/path/b", None)
    await db_session.commit()

    roots = await list_library_roots(db_session)
    assert len(roots) == 2
    assert roots[0].path == "/path/a" and roots[0].name == "Root A"
    assert roots[1].path == "/path/b" and roots[1].name is None

    by_id = await get_library_root_by_id(db_session, root1.id)
    assert by_id is not None and by_id.path == "/path/a"


@pytest.mark.asyncio
async def test_delete_library_root(db_session: AsyncSession) -> None:
    """Delete library root removes it from list."""
    root = await create_library_root(db_session, "/path/x", "X")
    await db_session.commit()
    await delete_library_root(db_session, root.id)
    await db_session.commit()
    roots = await list_library_roots(db_session)
    assert len(roots) == 0


@pytest.mark.asyncio
async def test_get_default_library_root_empty(db_session: AsyncSession) -> None:
    """get_default_library_root returns None when no roots."""
    default = await get_default_library_root(db_session)
    assert default is None


@pytest.mark.asyncio
async def test_create_track_and_get(db_session: AsyncSession) -> None:
    """Create track and get by id."""
    root = await create_library_root(db_session, "/lib", "Lib")
    await db_session.flush()
    track = await create_track(
        db_session,
        "song.mp3",
        "/lib/song.mp3",
        library_root_id=root.id,
        title="Song",
        artist="Artist",
        duration_seconds=120.5,
    )
    await db_session.commit()

    t = await get_track_by_id(db_session, track.id)
    assert t is not None
    assert t.filename == "song.mp3"
    assert t.title == "Song"
    assert t.duration_seconds == 120.5


@pytest.mark.asyncio
async def test_playlist_add_remove_reorder(db_session: AsyncSession) -> None:
    """Add tracks to playlist, reorder, remove."""
    root = await create_library_root(db_session, "/lib", "Lib")
    await db_session.flush()
    t1 = await create_track(db_session, "a.mp3", "/lib/a.mp3", library_root_id=root.id)
    t2 = await create_track(db_session, "b.mp3", "/lib/b.mp3", library_root_id=root.id)
    await db_session.commit()

    added = await add_track_to_playlist(db_session, t1.id)
    assert added is not None
    await add_track_to_playlist(db_session, t2.id)
    await db_session.commit()

    order = await get_ordered_track_ids(db_session)
    assert order == [t1.id, t2.id]

    await set_playlist_order(db_session, [t2.id, t1.id])
    await db_session.commit()
    order = await get_ordered_track_ids(db_session)
    assert order == [t2.id, t1.id]

    await remove_track_from_playlist(db_session, t1.id)
    await db_session.commit()
    order = await get_ordered_track_ids(db_session)
    assert order == [t2.id]


@pytest.mark.asyncio
async def test_playback_state_singleton(db_session: AsyncSession) -> None:
    """get_or_create_playback_state and set_playback_state."""
    state = await get_or_create_playback_state(db_session)
    await db_session.commit()
    assert state.id is not None
    assert state.is_playing is False

    await set_playback_state(db_session, is_playing=True, position_seconds=10.0)
    await db_session.commit()
    state2 = await get_or_create_playback_state(db_session)
    assert state2.id == state.id
    assert state2.is_playing is True
    assert state2.position_seconds == 10.0
