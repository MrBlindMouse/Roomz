"""Small async MPD client. Two sockets: commands, and idle.

MPD owns the live queue, the library index, and named playlists. This module
speaks the text protocol; it does not decode audio.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.config import server_timestamp_utc
from app.stream import stream_format

logger = logging.getLogger(__name__)


class MpdError(Exception):
    """MPD ACK, or the daemon is unreachable."""


def quote_arg(value: str) -> str:
    """Quote a protocol argument. MPD uses double quotes and backslash escapes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def encode_arg(arg: object) -> str:
    """Encode one command argument."""
    if isinstance(arg, str):
        return quote_arg(arg)
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, float):
        return f"{arg:.3f}"
    return str(arg)


def encode_command(name: str, *args: object) -> str:
    """Encode a single MPD command line, without the trailing newline."""
    return " ".join([name, *(encode_arg(arg) for arg in args)])


def parse_pairs(lines: list[str]) -> dict[str, str]:
    """Parse a flat `key: value` response (status, currentsong)."""
    out: dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        out[key] = value
    return out


def parse_objects(lines: list[str], start_key: str) -> list[dict[str, str]]:
    """Parse repeated records. A new record starts at start_key (usually `file`)."""
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key == start_key:
            if current is not None:
                items.append(current)
            current = {key: value}
        elif current is not None:
            current[key] = value
    if current is not None:
        items.append(current)
    return items


def plan_moves(current: list[int], desired: list[int]) -> list[tuple[int, int]]:
    """MPD `move FROM TO` steps that turn current song ids into desired.

    TO is the index in the playlist before the move. pop/insert matches MPD.
    """
    ids = list(current)
    moves: list[tuple[int, int]] = []
    for index, want in enumerate(desired):
        if index >= len(ids) or ids[index] == want:
            continue
        src = ids.index(want)
        moves.append((src, index))
        item = ids.pop(src)
        ids.insert(index, item)
    return moves


def repeat_commands(mode: int) -> list[str]:
    """0 off, 1 one track, 2 whole queue. MPD uses repeat + single."""
    if mode == 1:
        return [encode_command("repeat", 1), encode_command("single", 1)]
    if mode == 2:
        return [encode_command("repeat", 1), encode_command("single", 0)]
    return [encode_command("repeat", 0), encode_command("single", 0)]


def _repeat_mode(status: dict[str, str]) -> int:
    repeat = status.get("repeat") == "1"
    single = status.get("single") == "1"
    if repeat and single:
        return 1
    if repeat:
        return 2
    return 0


def _duration_of(song: dict[str, str]) -> float:
    raw = song.get("duration") or song.get("Time")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def song_to_library_track(song: dict[str, str]) -> dict[str, Any]:
    """One library file for the browse UI."""
    file = song.get("file") or ""
    parent = str(Path(file).parent)
    folder = "" if parent == "." else parent
    duration = _duration_of(song)
    return {
        "file": file,
        "title": song.get("Title") or Path(file).name,
        "artist": song.get("Artist") or "",
        "album": song.get("Album") or "",
        "duration_seconds": duration or None,
        "folder": folder,
    }


def playlist_items(songs: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Queue entries. `id` is the MPD song id."""
    items = []
    for song in songs:
        items.append(
            {
                "id": int(song.get("Id") or 0),
                "position": int(song.get("Pos") or 0),
                "file": song.get("file") or "",
                "title": song.get("Title") or Path(song.get("file") or "").name,
                "artist": song.get("Artist") or "",
                "duration_seconds": _duration_of(song) or None,
            }
        )
    return items


def build_status(
    status: dict[str, str],
    song: dict[str, str],
    playlist: list[dict[str, str]],
    now: float,
) -> dict[str, Any]:
    """Status message for every browser. The seek bar uses this; audio does not."""
    state = status.get("state") or "stop"
    song_id = status.get("songid")
    elapsed_raw = status.get("elapsed") or "0"
    try:
        elapsed = float(elapsed_raw)
    except ValueError:
        elapsed = 0.0
    duration = _duration_of(song) or _duration_of(status)
    return {
        "type": "status",
        "state": state,
        "is_playing": state == "play",
        "elapsed": elapsed,
        "duration": duration,
        "song_id": int(song_id) if song_id is not None else None,
        "title": song.get("Title") or (Path(song["file"]).name if song.get("file") else ""),
        "artist": song.get("Artist") or "",
        "file": song.get("file") or "",
        "random": status.get("random") == "1",
        "repeat": _repeat_mode(status),
        "server_timestamp": now,
        "playlist": playlist_items(playlist),
        "stream": stream_format(),
    }


def empty_status() -> dict[str, Any]:
    """Status when MPD cannot be reached. The socket still stays up."""
    return build_status({}, {}, [], server_timestamp_utc())


class MpdClient:
    """Command connection. Idle uses a second socket so commands are not stuck behind idle."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def close(self) -> None:
        """Drop the command socket."""
        async with self._lock:
            await self._drop()

    async def snapshot(self) -> dict[str, Any]:
        """Current status, currentsong, and queue."""
        status = parse_pairs(await self._command("status"))
        song = parse_pairs(await self._command("currentsong"))
        playlist = parse_objects(await self._command("playlistinfo"), "file")
        payload = build_status(status, song, playlist, server_timestamp_utc())
        from app.playlists import active_name

        payload["stored_playlist"] = active_name()
        try:
            payload["stored_playlists"] = await self.list_stored()
        except MpdError:
            payload["stored_playlists"] = []
        return payload

    async def list_library(self) -> list[dict[str, Any]]:
        """Every file MPD has indexed."""
        songs = parse_objects(await self._command("listallinfo"), "file")
        return [song_to_library_track(song) for song in songs]

    async def play(self) -> None:
        await self._command(encode_command("play"))

    async def pause(self) -> None:
        await self._command(encode_command("pause", 1))

    async def seek(self, seconds: float) -> None:
        await self._command(encode_command("seekcur", max(0.0, seconds)))

    async def next_track(self) -> None:
        await self._command(encode_command("next"))

    async def previous_track(self) -> None:
        await self._command(encode_command("previous"))

    async def set_random(self, on: bool) -> None:
        await self._command(encode_command("random", 1 if on else 0))

    async def set_repeat_mode(self, mode: int) -> None:
        await self._command_list(repeat_commands(mode))

    async def clear(self) -> None:
        await self._command(encode_command("clear"))

    async def add(self, uri: str) -> None:
        await self._command(encode_command("add", uri))

    async def addid(self, uri: str) -> int:
        lines = await self._command(encode_command("addid", uri))
        return int(parse_pairs(lines)["Id"])

    async def playid(self, song_id: int) -> None:
        await self._command(encode_command("playid", song_id))

    async def deleteid(self, song_id: int) -> None:
        await self._command(encode_command("deleteid", song_id))

    async def play_uri(self, uri: str) -> None:
        """Play this library file. Reuse the queue entry when it is already there."""
        for song in parse_objects(await self._command("playlistinfo"), "file"):
            if song.get("file") == uri and song.get("Id"):
                await self.playid(int(song["Id"]))
                return
        song_id = await self.addid(uri)
        await self.playid(song_id)

    async def reorder(self, desired_ids: list[int]) -> None:
        """Reorder the queue to these MPD song ids. Unknown ids are ignored."""
        songs = parse_objects(await self._command("playlistinfo"), "file")
        current = [int(song["Id"]) for song in songs if song.get("Id")]
        wanted = [song_id for song_id in desired_ids if song_id in current]
        moves = plan_moves(current, wanted)
        if not moves:
            return
        await self._command_list([encode_command("move", src, dest) for src, dest in moves])

    async def list_stored(self) -> list[str]:
        """Names of playlists saved in MPD's playlist directory."""
        songs = parse_objects(await self._command("listplaylists"), "playlist")
        return sorted(item["playlist"] for item in songs if item.get("playlist"))

    async def delete_stored(self, name: str) -> None:
        await self._command(encode_command("rm", name))

    async def store_queue_as(self, name: str) -> None:
        """Replace a stored playlist with the current queue."""
        from app.playlists import write_empty_playlist

        try:
            await self._command(encode_command("rm", name))
        except MpdError:
            logger.info("stored playlist %s was not there to replace", name)
        songs = parse_objects(await self._command("playlistinfo"), "file")
        if not songs:
            write_empty_playlist(name)
            return
        await self._command(encode_command("save", name))

    async def load_stored(self, name: str) -> None:
        """Replace the live queue with a stored playlist. Keep playing if it was playing."""
        status = parse_pairs(await self._command("status"))
        playing = status.get("state") == "play"
        await self.clear()
        await self._command(encode_command("load", name))
        if playing:
            await self.play()

    async def play_directory(self, uri: str) -> None:
        """Replace the live queue with one library directory and start it."""
        await self.clear()
        await self._command(encode_command("add", uri))
        await self.play()

    async def update_library(self) -> None:
        """Ask MPD to rescan the music directory and wait until it finishes."""
        await self._command(encode_command("update"))
        deadline = asyncio.get_running_loop().time() + 120.0
        while True:
            status = parse_pairs(await self._command("status"))
            if "updating_db" not in status:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise MpdError("MPD library update timed out")
            await asyncio.sleep(0.2)

    async def is_updating(self) -> bool:
        status = parse_pairs(await self._command("status"))
        return "updating_db" in status

    async def supervise_idle(self, on_change, stop: asyncio.Event) -> None:
        """Reconnect the idle socket until stop. on_change is called after each wake."""
        while not stop.is_set():
            try:
                await self._run_idle(on_change, stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info("MPD idle disconnected: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    async def _run_idle(self, on_change, stop: asyncio.Event) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 2.0
            )
        except Exception as exc:
            raise MpdError("MPD is down") from exc
        try:
            if not await _mpd_greeting_ok(reader, 2.0):
                raise MpdError("bad greeting")
            while not stop.is_set():
                writer.write(b"idle player playlist options mixer update\n")
                await writer.drain()
                await _read_response(reader)
                if stop.is_set():
                    return
                await on_change()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _command(self, line: str) -> list[str]:
        return await self._request(line + "\n")

    async def _command_list(self, commands: list[str]) -> list[str]:
        if not commands:
            return []
        payload = "command_list_begin\n" + "\n".join(commands) + "\ncommand_list_end\n"
        return await self._request(payload)

    async def _request(self, payload: str) -> list[str]:
        async with self._lock:
            await self._ensure()
            assert self._writer is not None and self._reader is not None
            try:
                self._writer.write(payload.encode())
                await self._writer.drain()
                return await _read_response(self._reader)
            except MpdError:
                raise
            except Exception as exc:
                await self._drop()
                raise MpdError("MPD is down") from exc

    async def _ensure(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        await self._drop()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 2.0
            )
        except Exception as exc:
            await self._drop()
            raise MpdError("MPD is down") from exc
        if not await _mpd_greeting_ok(self._reader, 2.0):
            await self._drop()
            raise MpdError("bad greeting")

    async def _drop(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _read_response(reader: asyncio.StreamReader) -> list[str]:
    lines: list[str] = []
    while True:
        raw = await reader.readline()
        if not raw:
            raise MpdError("connection closed")
        text = raw.decode("utf-8", "replace").rstrip("\r\n")
        if text == "OK":
            return lines
        if text.startswith("ACK"):
            raise MpdError(text)
        lines.append(text)


async def _mpd_greeting_ok(reader: asyncio.StreamReader, timeout: float) -> bool:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout)
        return line.startswith(b"OK MPD")
    except Exception:
        return False


async def probe_mpd(host: str, port: int, timeout: float = 0.5) -> bool:
    """True when a TCP greeting starts with `OK MPD`. Does not keep the socket."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:
        return False
    try:
        return await _mpd_greeting_ok(reader, timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
