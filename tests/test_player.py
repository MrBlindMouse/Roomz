"""Unit tests for the Player: load_from_session, get_state_for_snapshot, apply_command."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import add_track_to_playlist, create_library_root, create_track, set_playback_state
from app.player import Player


@pytest.mark.asyncio
async def test_player_load_from_session_empty(db_session: AsyncSession) -> None:
    """Load from empty DB leaves default state."""
    player = Player()
    await player.load_from_session(db_session)
    await db_session.commit()
    state = player.get_state_for_snapshot()
    assert state["current_track_id"] is None
    assert state["is_playing"] is False
    assert state["position_seconds"] == 0.0


@pytest.mark.asyncio
async def test_player_apply_play_pause(db_session: AsyncSession) -> None:
    """apply_command play then pause updates state and returns broadcast dict."""
    player = Player()
    await player.load_from_session(db_session)
    await db_session.commit()

    out_play = await player.apply_command(db_session, "play")
    await db_session.commit()
    assert out_play is not None
    assert out_play["type"] == "play"
    assert out_play["is_playing"] is True
    assert player.get_state_for_snapshot()["is_playing"] is True

    out_pause = await player.apply_command(db_session, "pause")
    await db_session.commit()
    assert out_pause is not None
    assert out_pause["type"] == "pause"
    assert out_pause["is_playing"] is False
    assert player.get_state_for_snapshot()["is_playing"] is False


@pytest.mark.asyncio
async def test_player_apply_seek(db_session: AsyncSession) -> None:
    """apply_command seek updates position."""
    player = Player()
    await player.load_from_session(db_session)
    await db_session.commit()

    out = await player.apply_command(db_session, "seek", position_seconds=42.5)
    await db_session.commit()
    assert out is not None
    assert out["type"] == "seek"
    assert out["position"] == 42.5
    assert player.get_state_for_snapshot()["position_seconds"] == 42.5


@pytest.mark.asyncio
async def test_player_apply_set_track(db_session: AsyncSession) -> None:
    """apply_command set_track sets current track and position 0."""
    root = await create_library_root(db_session, "/tmp/roomz_test_lib", "Test")
    await db_session.flush()
    track = await create_track(
        db_session, "foo.mp3", "/tmp/roomz_test_lib/foo.mp3", library_root_id=root.id
    )
    await db_session.commit()

    player = Player()
    await player.load_from_session(db_session)
    await db_session.commit()

    out = await player.apply_command(db_session, "set_track", track_id=track.id)
    await db_session.commit()
    assert out is not None
    assert out["type"] == "set_track"
    assert out["track_id"] == track.id
    assert out["position"] == 0
    assert player.get_state_for_snapshot()["current_track_id"] == track.id
    assert player.get_state_for_snapshot()["position_seconds"] == 0.0


@pytest.mark.asyncio
async def test_player_apply_unknown_returns_none(db_session: AsyncSession) -> None:
    """apply_command with unknown type returns None."""
    player = Player()
    await player.load_from_session(db_session)
    await db_session.commit()

    out = await player.apply_command(db_session, "unknown_typ")
    assert out is None
