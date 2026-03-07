"""Unit tests for clock_sync: server_timestamp_utc."""

import time

import pytest

from app.clock_sync import server_timestamp_utc


def test_server_timestamp_utc_returns_float() -> None:
    """server_timestamp_utc returns a float (Unix seconds)."""
    t = server_timestamp_utc()
    assert isinstance(t, float)
    assert t > 0


def test_server_timestamp_utc_increases() -> None:
    """Consecutive calls return non-decreasing values."""
    t1 = server_timestamp_utc()
    time.sleep(0.001)
    t2 = server_timestamp_utc()
    assert t2 >= t1
