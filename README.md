# Roomz

LAN-synchronized single-stream audio playback: one global stream, all devices in sync.

- **Single global stream** — no rooms, no codes. One playlist and one playback state for the entire LAN.
- **Sub-10 ms sync** on LAN using NTP-style clock sync and Web Audio API.
- **Per-device delay** (0–500 ms) to compensate for speaker placement.
- Upload or scan `./data/music/`, collaborative playlist, simple chat.

## Run

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://<this-machine-ip>:8000` in a browser.

## Multi-device sync test

1. Run the server on a machine on your LAN (e.g. `192.168.1.10`).
2. On multiple devices (phones, laptops, same LAN), open `http://192.168.1.10:8000`.
3. Add or scan music, press play. All clients should play the same track at the same position.
4. Use **Speaker delay (ms)** on each device to align with physical speaker placement (e.g. farther speakers get more delay).

## Adding music

- **Upload**: Use the **Upload** button and choose MP3/WAV/OGG/M4A/FLAC files. They are saved to `./data/music/` and added to the playlist.
- **Existing files**: Put files in `./data/music/`, then click **Scan folder**. New files are discovered and added to the DB and playlist.

## Per-device delay

Use the **Speaker delay compensation (ms)** slider (0–500) in the header to fine-tune each device. No server round-trip; applies instantly to the sync position so playback stays aligned with others.

## Sync accuracy

On a typical LAN, sync within **&lt;10 ms** is achievable with Web Audio API and clock sync. Browser and device output latency can add a constant offset; use the delay slider to compensate so all speakers sound in phase.

## Project structure

```
Roomz/
├── pyproject.toml
├── uv.lock
├── app/
│   ├── main.py          # FastAPI app, /music, /ws, static mount
│   ├── models.py
│   ├── schemas.py
│   ├── crud.py
│   ├── db.py
│   ├── ws_manager.py
│   ├── clock_sync.py
│   ├── audio_utils.py
│   └── routers/
│       └── api.py       # upload, scan, playlist CRUD
├── data/
│   ├── music/           # audio files
│   └── app.db           # SQLite
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
└── alembic/
```

## Tech stack

- **Backend**: FastAPI, Uvicorn, SQLAlchemy 2.0 (async), Alembic, SQLite, mutagen, aiofiles. Managed with **uv**.
- **Frontend**: Vanilla HTML/CSS/JS, Tailwind via CDN, Web Audio API (with &lt;audio&gt; fallback).

No auth (LAN-only). Production run: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`.
