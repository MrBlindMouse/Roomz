"""Unit tests for sync_tick broadcast position extrapolation."""

from app.player import compute_sync_tick_broadcast_position_seconds


def test_paused_returns_frozen_position_ignores_time() -> None:
    state = {
        "position_seconds": 42.5,
        "is_playing": False,
        "last_update_server_timestamp": 1000.0,
    }
    assert compute_sync_tick_broadcast_position_seconds(state, ts=2000.0) == 42.5


def test_playing_extrapolates_from_last_update() -> None:
    state = {
        "position_seconds": 0.0,
        "is_playing": True,
        "last_update_server_timestamp": 1000.0,
    }
    assert compute_sync_tick_broadcast_position_seconds(state, ts=1030.0) == 30.0


def test_playing_with_seek_anchor() -> None:
    state = {
        "position_seconds": 120.0,
        "is_playing": True,
        "last_update_server_timestamp": 2000.0,
    }
    assert compute_sync_tick_broadcast_position_seconds(state, ts=2005.0) == 125.0


def test_playing_zero_last_ts_uses_ts_no_spurious_delta() -> None:
    state = {
        "position_seconds": 10.0,
        "is_playing": True,
        "last_update_server_timestamp": 0.0,
    }
    assert compute_sync_tick_broadcast_position_seconds(state, ts=500.0) == 10.0


def test_clamps_negative_result_to_zero() -> None:
    state = {
        "position_seconds": 5.0,
        "is_playing": True,
        "last_update_server_timestamp": 100.0,
    }
    assert compute_sync_tick_broadcast_position_seconds(state, ts=90.0) == 0.0
