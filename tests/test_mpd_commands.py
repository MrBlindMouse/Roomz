"""MPD command encoding and queue-move planning. No daemon required."""

from app.mpd_client import (
    build_status,
    encode_command,
    parse_objects,
    plan_moves,
    quote_arg,
)


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
