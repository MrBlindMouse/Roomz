"""Playlist names, folder URIs, and the remembered active name."""

import pytest

from app.mpd_client import MpdError
from app.playlists import (
    active_name,
    clear_active_name,
    folder_uri,
    playlist_name,
    set_active_name,
    write_empty_playlist,
)


def test_playlist_name_rejects_paths() -> None:
    assert playlist_name(" Night ") == "Night"
    with pytest.raises(MpdError):
        playlist_name("../secret")
    with pytest.raises(MpdError):
        playlist_name("a/b")


def test_folder_uri_is_relative_to_the_library() -> None:
    assert folder_uri("") == "/"
    assert folder_uri("live/night") == "live/night"
    with pytest.raises(MpdError):
        folder_uri("../outside")


def test_active_name_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.playlists.active_path", lambda: tmp_path / "active-playlist")
    assert active_name() == ""
    set_active_name("Night")
    assert active_name() == "Night"
    clear_active_name()
    assert active_name() == ""


def test_empty_playlist_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.playlists.playlist_directory", lambda: tmp_path)
    write_empty_playlist("Night")
    assert (tmp_path / "Night.m3u").read_text(encoding="utf-8") == "#EXTM3U\n"
