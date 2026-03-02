# Roomz — Agent overview

## What this project is

Roomz is a **single-global-stream**, LAN-synchronized audio player. One FastAPI server streams one audio feed to many browser clients; all play the same track at the same position with sub-10 ms sync on LAN.

- **No rooms or codes**: one playlist, one playback state for the whole LAN.
- **Server is authoritative**: playback state (track, position, playing) lives in SQLite and is broadcast over WebSocket.
- **Clients sync via NTP-style clock sync** and apply per-device delay (0–500 ms) for speaker placement.

## Stack

- **Backend**: FastAPI, Uvicorn, SQLAlchemy 2.0 (async, aiosqlite), Alembic, SQLite at `data/app.db`. Dependencies managed with **uv** (`pyproject.toml`, `uv.lock`). No Docker.
- **Frontend**: Vanilla HTML/CSS/JS, Tailwind Play CDN, single SPA at `/` (StaticFiles with `html=True`). Web Audio API for playback; &lt;audio&gt; fallback with 500 ms position correction.
- **Audio**: Files in `data/music/`. Metadata via mutagen. Serving via `GET /music/{filename}` with Range/ETag for seeking.

## Key modules

- `app/main.py` — FastAPI app, `GET /music/{filename}` (Range), `WebSocket /ws`, static mount for SPA.
- `app/ws_manager.py` — Global `ConnectionManager`: connect, disconnect, broadcast.
- `app/clock_sync.py` — Server timestamp for client offset calculation (no server state).
- `app/routers/api.py` — `POST /api/upload`, `POST /api/scan`, `GET /api/playlist`, playlist items/reorder.
- `app/crud.py` — Tracks, playlist order, playback state (singleton).
- `app/models.py` — Track, PlaylistOrder, PlaybackState.
- `app/audio_utils.py` — Mutagen metadata extraction (async-safe).

## Conventions

- Prefer simplicity and long-term maintainability. Self-documenting code, type hints, docstrings. Black/ruff.
- Async everywhere; no blocking calls. Defensive error handling and structured logging.
- Paths under `data/music/` only; resolve and reject path traversal. Validate upload types (audio allowlist).
- No auth (LAN-only). Upload rate limit in API.

## Running

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```
