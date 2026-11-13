"""Album art path checks and HTTP endpoint."""

from app.art import folder_art_path, image_media_type, resolve_library_file
from app.main import app as main_app
from app.mpd_access import get_mpd
from fastapi.testclient import TestClient


def test_resolve_library_file_rejects_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(tmp_path))
    try:
        resolve_library_file("../outside.mp3")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_folder_art_path_finds_cover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(tmp_path))
    album = tmp_path / "live"
    album.mkdir()
    (album / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (album / "track.mp3").write_bytes(b"x")
    found = folder_art_path("live/track.mp3")
    assert found == album / "cover.jpg"


def test_image_media_type_detects_jpeg() -> None:
    assert image_media_type(b"\xff\xd8\xff\x00") == "image/jpeg"


def test_get_art_folder_cover(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(tmp_path))

    class ArtMpd:
        async def fetch_album_art(self, uri: str) -> bytes | None:
            return None

    main_app.dependency_overrides[get_mpd] = lambda: ArtMpd()
    try:
        album = tmp_path / "a"
        album.mkdir()
        (album / "folder.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
        (album / "song.mp3").write_bytes(b"x")
        response = client.get("/api/art", params={"file": "a/song.mp3"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
        assert response.content.startswith(b"\xff\xd8")
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)


def test_get_art_missing_is_404(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROOMZ_LIBRARY_BASE", str(tmp_path))

    class ArtMpd:
        async def fetch_album_art(self, uri: str) -> bytes | None:
            return None

    main_app.dependency_overrides[get_mpd] = lambda: ArtMpd()
    try:
        (tmp_path / "lonely.mp3").write_bytes(b"x")
        response = client.get("/api/art", params={"file": "lonely.mp3"})
        assert response.status_code == 404
    finally:
        main_app.dependency_overrides.pop(get_mpd, None)
