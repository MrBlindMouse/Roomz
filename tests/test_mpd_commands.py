"""MPD command encoding and queue-move planning. No daemon required."""

from app.mpd_client import (
    MpdClient,
    MpdError,
    _transient_mpd_error,
    build_status,
    encode_command,
    parse_objects,
    plan_moves,
    quote_arg,
)


def test_transient_mpd_error() -> None:
    assert _transient_mpd_error(MpdError("connection closed")) is True
    assert _transient_mpd_error(MpdError("MPD is down")) is True
    assert _transient_mpd_error(MpdError("ACK [50@0] {} no such playlist")) is False


async def test_mpd_request_retries_connection_closed(monkeypatch) -> None:
    client = MpdClient("127.0.0.1", 6600)
    attempts = 0

    class FakeWriter:
        def write(self, data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def is_closing(self) -> bool:
            return False

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    client._reader = object()
    client._writer = FakeWriter()

    async def fake_ensure() -> None:
        pass

    async def fake_drop() -> None:
        pass

    async def fake_read(reader) -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MpdError("connection closed")
        return ["state: stop"]

    monkeypatch.setattr(client, "_ensure", fake_ensure)
    monkeypatch.setattr(client, "_drop", fake_drop)
    monkeypatch.setattr("app.mpd_client._read_response", fake_read)

    lines = await client._request("status\n")
    assert attempts == 2
    assert lines == ["state: stop"]


def test_encode_play_pause_seek() -> None:
    """play, pause, and seekcur encode as MPD command lines."""
    assert encode_command("play") == "play"
    assert encode_command("pause", 1) == "pause 1"
    assert encode_command("seekcur", 12.5) == "seekcur 12.500"
    assert encode_command("add", "my song.mp3") == 'add "my song.mp3"'
    assert quote_arg('a"b\\c') == '"a\\"b\\\\c"'


def test_plan_moves_matches_mpd_index_semantics() -> None:
    """One move puts the last id first; an unchanged queue sends nothing."""
    assert plan_moves([1, 2, 3], [3, 1, 2]) == [(2, 0)]
    assert plan_moves([1, 2], [1, 2]) == []


def test_parse_objects_splits_on_file() -> None:
    """playlistinfo records start at each `file:` line."""
    songs = parse_objects(
        [
            "file: a.mp3",
            "Title: A",
            "Id: 1",
            "file: b.mp3",
            "Id: 2",
        ],
        "file",
    )
    assert songs[0]["Title"] == "A"
    assert songs[1]["Id"] == "2"


def test_build_status_repeat_and_playing() -> None:
    """Repeat+single is mode 1, and state play is the only playing state."""
    message = build_status(
        {"state": "play", "elapsed": "1.5", "repeat": "1", "single": "1", "songid": "7"},
        {"file": "a.mp3", "Title": "A", "Artist": "B", "duration": "30"},
        [{"file": "a.mp3", "Title": "A", "Id": "7", "Pos": "0", "duration": "30"}],
        100.0,
    )
    assert message["type"] == "status"
    assert message["is_playing"] is True
    assert message["repeat"] == 1
    assert message["song_id"] == 7
    assert message["elapsed"] == 1.5
    assert message["server_timestamp"] == 100.0
    assert message["playlist"][0]["id"] == 7
    assert message["stream"]["rate"] == 44100


def test_build_status_includes_rich_metadata() -> None:
    message = build_status(
        {"state": "play", "songid": "3"},
        {
            "file": "a/song.mp3",
            "Title": "Song",
            "Artist": "Band",
            "Album": "Record",
            "Genre": "Rock",
            "Date": "1999-01-01",
            "Track": "2/10",
        },
        [],
        0.0,
    )
    assert message["album"] == "Record"
    assert message["genre"] == "Rock"
    assert message["track"] == "2"
    assert message["year"] == "1999"
