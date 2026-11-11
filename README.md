# Roomz

LAN-synchronized single-stream audio. MPD plays one timeline. Every browser on the LAN hears that stream, and each device can shift it by a few milliseconds.

- **One stream.** No rooms, no per-device playlists. Play, pause, seek, and the queue are shared.
- **Per-device delay** (−250..250 ms), remembered in the browser. Positive delay plays later. Use it by ear when a speaker sits farther away.
- **Automatic alignment** from frame timestamps and a short clock-sync burst. The seek bar follows MPD. It does not drive the audio.
- **One music folder.** Upload or scan. MPD indexes the files.

## The Why

I created Roomz because I was annoyed that I cannot connect more than 1 BT Speaker to my phone for wider coverage.

Now I can play music throughout the house from multiple devices.

I'm pretty sure something else already exists for this. . . but so-what.

### Future plans

- Add a centralized control, for managing per device delays and volume from one client.
- Create a client for rpi or headless devices.
- Maybe add an announcement function to the main client, to broadcast voice over the stream.

### Warning

Roomz is meant to be run on a private LAN/Home network. There is no security built in, and anybody connected to the network has full access. If enough interest is shown for it, then security might be added at a later date.

## Run

MPD is the player. Roomz does not start it.

1. Install MPD (`mpd` from your distro).
2. Edit the absolute paths in [`mpd/mpd.conf`](mpd/mpd.conf). `music_directory` must be `ROOMZ_LIBRARY_BASE` (default: this repo's `data/`). The FIFO path must be `ROOMZ_FIFO` (default: `/tmp/roomz-audio.fifo`).
3. `mkdir -p` the playlist directory from that config.
4. `mpd /absolute/path/to/Roomz/mpd/mpd.conf`

Copy `.env.example` to `.env` if you need a non-default host, port, FIFO, or music directory. The app loads `.env` at startup.

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://<this-machine-ip>:8000`. `/health` stays unhealthy until MPD answers and the FIFO exists.

The stream is 16-bit stereo PCM at 44100 Hz, about 176 KB/s per browser. Headphones and speakers on this server stay quiet unless a browser is open here too: MPD's only output is the FIFO.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run black --check .
```

## Multi-device sync test

1. Start MPD, then Roomz, on a machine on your LAN.
2. On two devices, open `http://<this-machine-ip>:8000`. Click the page once so the browser allows audio.
3. Add or scan music, press play. Both play the same stream without playhead jumps.
4. Move **Speaker delay (ms)** on one device. Only that device shifts.

## Adding music

- **Upload** writes into the music directory, asks MPD to rescan, and appends the file to the queue.
- **Scan** runs an MPD database update.
- The library panel lists what MPD has indexed. **Add** queues a file. Clicking the row plays it.

## Per-device delay

The slider is local to the browser (`localStorage`). It changes when the next PCM frames are played. It does not seek the file and it does not move anyone else's speakers. The house volume is not on this slider either: the volume control is a local gain.

## Sync

Each ~20 ms frame carries the server time of its first sample. The browser schedules it on an `AudioContext` at that time, plus a 300 ms buffer, plus this device's delay. A jump in the timestamps (seek, pause, track change) flushes the buffer. Clock offset uses the lowest-RTT sample from a short burst.

What the browser will not tell us — output delay past `AudioContext.outputLatency` — is the job of the slider. A tab in the background is not a reliable speaker.

## Project structure

```
Roomz/
├── pyproject.toml
├── uv.lock
├── mpd/mpd.conf       # sample MPD config (FIFO output)
├── tests/
├── app/
│   ├── main.py        # /health, /ws, static mount
│   ├── mpd_client.py  # MPD protocol
│   ├── stream.py      # FIFO frames
│   ├── config.py
│   ├── mpd_access.py
│   ├── ws_manager.py
│   └── routers/api.py # library, upload, scan
├── data/              # music directory by default
└── frontend/
    ├── index.html
    └── static/
```

## Tech stack

- **Player**: MPD, one FIFO output.
- **Backend**: FastAPI, Uvicorn, aiofiles. Managed with **uv**.
- **Frontend**: Vanilla HTML/CSS/JS, Web Audio API.

No auth (LAN-only).
