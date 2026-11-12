# Roomz

LAN-synchronized single-stream audio. MPD plays one timeline. Every browser on the LAN hears that stream, and each device can shift it by a few milliseconds.

- **One stream.** No rooms, no per-device playlists. Play, pause, seek, and the queue are shared.
- **Per-device delay** (−250..250 ms), remembered in the browser. Positive delay plays later. Use it by ear when a speaker sits farther away.
- **Automatic alignment** from frame timestamps and a short clock-sync burst. The seek bar follows MPD. It does not drive the audio.
- **One music folder.** Upload or scan. MPD indexes the files.

## The Why

I created Roomz because I was annoyed that I cannot connect more than 1 BT Speaker to my phone for wider coverage.

Now I can play music throughout my house from multiple devices.

I'm pretty sure something else already exists for this. . . but so-what.

### Future plans

Tracked in [TODO.md](TODO.md).

- Add a centralized control, for managing per device delays and volume from one client.
- Create a client for rpi or headless devices.
- Maybe add an announcement function to the main client, to broadcast voice over the stream.

### Warning

Roomz is meant to be run on a private LAN/Home network. There is no security built in, and anybody connected to the network has full access. If enough interest is shown for it, then security might be added at a later date.

## Run

MPD is the player. Roomz does not start it.

Template: [mpd/mpd.conf.example](mpd/mpd.conf.example). Your machine-specific copy is `mpd/mpd.conf` (gitignored; **not** `/etc/mpd.conf` unless you put it there yourself):

```bash
cp mpd/mpd.conf.example mpd/mpd.conf
```

`mpd/mpd.conf` is the only file for the music directory, FIFO, and MPD address. Roomz reads those from it. [.env.example](.env.example) is for Roomz logging, or `ROOMZ_MPD_CONF` when MPD was not started with the repo file.

1. Install MPD (`mpd` from your distro).
2. Edit `mpd/mpd.conf`: replace every `/absolute/path/to/Roomz` with a real path. `music_directory` is where uploads go. The fifo `path` is what Roomz reads. `port` and `bind_to_address` are where Roomz connects.
3. Create the playlist directory named in that file, for example:
   ```bash
   mkdir -p data/playlists
   ```
4. Put audio files in `music_directory`, then start MPD with this file:
   ```bash
   mpd "$(pwd)/mpd/mpd.conf"
   ```
5. Start Roomz from the repo root ([run.py](run.py) or uvicorn):
   ```bash
   uv sync
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

Open `http://<this-machine-ip>:8000` in a browser. [GET /health](app/main.py) returns 200 when that config is readable, MPD answers, and the FIFO exists. The page shows the same problems.

The stream is 16-bit stereo PCM at 44100 Hz, about 176 KB/s per browser ([app/stream.py](app/stream.py)). Headphones and speakers on this server stay quiet unless a browser is open here too: MPD’s only output is the FIFO.

## Development

From [pyproject.toml](pyproject.toml):

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run black --check .
```

Tests live under [tests/](tests/). Operator and agent notes: [AGENTS.md](AGENTS.md).

## Multi-device sync test

1. Start MPD with `mpd/mpd.conf`, then Roomz, on a machine on your LAN.
2. On two devices, open `http://<this-machine-ip>:8000`. Click the page once so the browser allows audio ([frontend/static/app.js](frontend/static/app.js)).
3. Add or scan music, press play. Both play the same stream without playhead jumps.
4. Move **Speaker delay (ms)** on one device. Only that device shifts.

## Adding music

HTTP API: [app/routers/api.py](app/routers/api.py).

- **Upload** writes into the music directory, asks MPD to rescan, and appends the file to the queue.
- **Scan** runs an MPD database update.
- The library panel lists what MPD has indexed. **Add** queues a file. Clicking the row plays it.

## Per-device delay

The slider is local to the browser (`localStorage`). It changes when the next PCM frames are played. It does not seek the file and it does not move anyone else's speakers. The house volume is not on this slider either: the volume control is a local gain.

## Sync

Each 100 ms chunk carries an epoch, a sequence number, and the server time of its first sample. The browser places the first chunk of an epoch on an `AudioContext` at that time, plus a 5 second lead, plus this device's delay. About one second of following chunks is copied into a single buffer at the sound card's sample rate, and those longer buffers play as one chain. The lead is also how long the server keeps each chunk: a browser that missed one asks for it before it is due. Pause, resume, and a seek flush the chain. A normal track change does not. Pressing play, seek, or resume is silent for about 5 seconds while the lead fills.

**Slow network** on one browser asks the server for Opus (five 20 ms frames per chunk, about 64 kbps) instead of raw PCM. Other speakers stay on PCM. If Opus is not available, that browser stays on PCM.

The clock is measured again every 5 seconds, using the lowest-RTT sample of a short burst. A change under 5 ms is ignored. A change under 150 ms is followed a quarter of the way, so the rooms do not click on every measurement. A larger error snaps. Output delay past `AudioContext.outputLatency` is still the slider's job. A hidden tab is not a reliable speaker.

## Project structure

| Path | Role |
|------|------|
| [mpd/mpd.conf.example](mpd/mpd.conf.example) | MPD template; copy to `mpd/mpd.conf` and edit paths |
| [app/main.py](app/main.py) | `/health`, WebSocket `/ws`, static SPA |
| [app/mpd_client.py](app/mpd_client.py) | MPD protocol client |
| [app/stream.py](app/stream.py) | FIFO reader, 100 ms chunks, Opus payload |
| [app/config.py](app/config.py) | Music directory, MPD address, and FIFO from `mpd.conf` |
| [app/ws_manager.py](app/ws_manager.py) | WebSocket fanout |
| [app/routers/api.py](app/routers/api.py) | `/api/library`, upload, scan |
| [frontend/index.html](frontend/index.html) | SPA shell |
| [frontend/static/app.js](frontend/static/app.js) | AudioContext scheduler, UI |
| [data/](data/) | Music directory only when `mpd.conf` does not set one |
| [TODO.md](TODO.md) | Gaps and implementation backlog |

Also: [pyproject.toml](pyproject.toml), [uv.lock](uv.lock), [run.py](run.py), [tests/](tests/).

## Tech stack

- **Player**: MPD, one FIFO output (`mpd/mpd.conf` from [mpd/mpd.conf.example](mpd/mpd.conf.example)).
- **Backend**: FastAPI, Uvicorn, aiofiles. Managed with **uv** ([pyproject.toml](pyproject.toml)).
- **Frontend**: Vanilla HTML/CSS/JS, Web Audio API ([frontend/](frontend/)).

No auth (LAN-only).
