# Roomz — Agent overview

## What this project is

Roomz is a **single-global-stream**, LAN-synchronized audio player. One FastAPI server streams one audio feed to many browser clients; all play the same track at the same position with sub-10 ms sync on LAN.

- **No rooms or codes**: one playlist, one playback state for the whole LAN.
- **Server is authoritative**: playback state (track, position, playing) lives in SQLite and is broadcast over WebSocket.
- **Clients sync via NTP-style clock sync** and apply per-device delay (0–500 ms) for speaker placement.

## Stack

- **Backend**: FastAPI, Uvicorn, SQLAlchemy 2.0 (async, aiosqlite), Alembic, SQLite at `data/app.db`. Dependencies managed with **uv** (`pyproject.toml`, `uv.lock`). No Docker.
- **Frontend**: Vanilla HTML/CSS/JS, single SPA at `/` (StaticFiles with `html=True`). CSS and JS live in `frontend/static/`; layered vanilla CSS (reset, base, colors, layout, components); no Tailwind. Web Audio API for playback; &lt;audio&gt; fallback with 500 ms position correction.
- **Audio**: Multiple user-chosen library folders (under `ROOMZ_LIBRARY_BASE` or `data/`). Metadata via mutagen. Serving via `GET /music/track/{track_id}` with Range/ETag. Scan is recursive.

## Key modules

- `app/main.py` — FastAPI app, `GET /music/track/{track_id}` (Range), `WebSocket /ws`, static mount for SPA.
- `app/config.py` — `LIBRARY_BASE`, `validate_library_path()` for allowed roots.
- `app/ws_manager.py` — Global `ConnectionManager`: connect, disconnect, broadcast.
- `app/clock_sync.py` — Server timestamp for client offset calculation (no server state).
- `app/routers/api.py` — `GET/POST/DELETE /api/library-roots`, `POST /api/upload?root_id=`, `POST /api/scan?root_id=`, playlist.
- `app/crud.py` — Library roots, tracks (with `library_root_id`, `filepath`), playlist, playback state.
- `app/models.py` — LibraryRoot, Track, PlaylistOrder, PlaybackState.
- `app/schemas.py` — Pydantic schemas for REST API (library roots, tracks, playlist, playback state).
- `app/audio_utils.py` — Mutagen metadata extraction (async-safe).

## Conventions

- Prefer simplicity and long-term maintainability. Self-documenting code, type hints, docstrings. Black/ruff.
- Async everywhere; no blocking calls. Defensive error handling and structured logging.
- Library paths must be under `LIBRARY_BASE` (env `ROOMZ_LIBRARY_BASE` or `data/`). Validate upload types (audio allowlist).
- No auth (LAN-only). Upload rate limit in API.

## Running

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```
