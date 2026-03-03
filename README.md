# Roomz

LAN-synchronized single-stream audio playback: one global stream, all devices in sync.

- **Single global stream** — no rooms, no codes. One playlist and one playback state for the entire LAN.
- **Sub-10 ms sync** on LAN using NTP-style clock sync and Web Audio API.
- **Per-device delay** (0–500 ms) to compensate for speaker placement.
- **Multiple library folders** — add paths from the UI (under an allowed base); scan recursively. Upload to a chosen folder.
- Upload or scan, collaborative playlist, simple chat.

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

- **Library folders**: In **Library folders**, add a path (and optional name). Paths must be under the allowed base (default: project `data/`; set `ROOMZ_LIBRARY_BASE` to allow e.g. `/home/you/Music`). Remove a folder only when it has no tracks.
- **Upload**: Choose a destination folder in the **Upload to** dropdown, then **Upload** and select MP3/WAV/OGG/M4A/FLAC files. They are saved under that folder and added to the playlist.
- **Scan**: **Scan all** scans every library folder recursively; or click **Scan** next to a folder to scan only that one. New files are added to the DB and playlist.

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
│   ├── main.py          # FastAPI app, /music/track/{id}, /ws, static mount
│   ├── config.py        # LIBRARY_BASE, validate_library_path
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
│   └── static/          # CSS and JS assets
│       ├── app.js
│       ├── colors.css
│       ├── _reset.css
│       ├── base.css
│       ├── layout.css
│       └── components.css
└── alembic/
```

## Tech stack

- **Backend**: FastAPI, Uvicorn, SQLAlchemy 2.0 (async), Alembic, SQLite, mutagen, aiofiles. Managed with **uv**.
- **Frontend**: Vanilla HTML/CSS/JS, layered vanilla CSS (reset, base, colors, layout, components), Web Audio API (with &lt;audio&gt; fallback).

No auth (LAN-only). Production run: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`.
