# Roomz — Agent overview

## What this project is

Roomz is a **single-global-stream**, LAN-synchronized audio player. MPD decodes one timeline. Roomz reads that PCM FIFO, stamps each frame, and every browser plays the same frames.

- **No rooms or codes**: one queue, one stream for the whole LAN.
- **MPD is the player**: play, pause, seek, the live queue, the library, and named playlists live in MPD. Roomz only remembers which stored playlist is active. Playing a folder replaces the live queue and does not save a playlist.
- **Clients only shift delay**: NTP-style clock sync plus the frame timestamps schedule playback. Each browser adds its own delay (−250..250 ms), stored locally. Volume is local too.

Browser output delay past `AudioContext.outputLatency` is what the slider is for. A hidden tab is not a speaker.

## Stack

- **Backend**: FastAPI, Uvicorn. Dependencies managed with **uv** (`pyproject.toml`, `uv.lock`). No Docker. MPD is a separate process (local `mpd/mpd.conf` from `mpd/mpd.conf.example`); Roomz does not start it.
- **Frontend**: Vanilla HTML/CSS/JS, single SPA at `/` (StaticFiles with `html=True`). CSS and JS live in `frontend/static/`; layered vanilla CSS (reset, base, colors, layout, components); no Tailwind. Web Audio API schedules the PCM frames.
- **Audio**: One music directory, taken from `music_directory` in `mpd/mpd.conf`. MPD indexes it. PCM is 44100 Hz, 16-bit stereo, ~20 ms frames, over the WebSocket.

## Key modules

- `app/main.py` — FastAPI app, `GET /health` (MPD greeting and FIFO), `WebSocket /ws` (JSON control + binary PCM), static mount.
- `app/mpd_client.py` — Async MPD text protocol: commands on one socket, `idle` on another.
- `app/stream.py` — FIFO reader, frame size, timestamp packing.
- `app/config.py` — Reads music directory, MPD host/port, and FIFO from `mpd.conf`. `server_timestamp_utc()`.
- `app/ws_manager.py` — Global `ConnectionManager`: text and binary broadcast.
- `app/mpd_access.py` — Shared `app.state.mpd` lookup for HTTP and WebSocket.
- `app/routers/api.py` — `GET /api/library`, `POST /api/upload`, `POST /api/scan`.
- `app/schemas.py` — Pydantic schemas for the library API.
- `app/logging_config.py` — `configure_logging()` from env `LOG_LEVEL` (default INFO), `LOG_FORMAT` (text | json).
- `frontend/static/app.js` — AudioContext scheduler, delay slider, transport.

## Conventions

- Prefer simplicity and long-term maintainability. Self-documenting code, type hints, docstrings. Black/ruff.
- Async everywhere; no blocking calls. Defensive error handling and centralized logging (env: `LOG_LEVEL`, `LOG_FORMAT`).
- Uploads go into `music_directory` from `mpd/mpd.conf`. Validate upload types (audio allowlist).
- No auth (LAN-only). Upload rate limit in API.
- **CORS**: Same-origin only; no CORSMiddleware. If the frontend is served from another origin (e.g. dev server), add explicit CORS in `app/main.py`.

## Running

Install MPD, `cp mpd/mpd.conf.example mpd/mpd.conf`, edit absolute paths, create the playlist directory, and start MPD. Then:

```bash
uv sync
uv run python run.py
```

Or:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`/health` is 503 until MPD is accepting connections and the FIFO exists.

## Testing

```bash
uv sync --extra dev
uv run pytest tests/ -v
uv run pytest tests/ --cov=app --cov-report=term-missing   # with coverage
```

Coverage is configured in `pyproject.toml` (`[tool.coverage.run]`, `[tool.coverage.report]`); minimum threshold is enforced (fail_under). Tests do not need a running MPD.
