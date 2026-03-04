"""FastAPI app: API, WebSocket, /music Range serving, SPA static files."""

import json
import logging
from pathlib import Path

import aiofiles
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.audio_utils import get_mime_for_filename
from app.clock_sync import server_timestamp_utc
from app.crud import (
    add_track_to_playlist,
    get_or_create_playback_state,
    get_ordered_track_ids,
    get_playlist_item_dicts,
    get_track_by_id,
    remove_track_from_playlist,
    set_playback_state,
    set_playlist_order,
)
from app.config import LIBRARY_BASE
from app.crud import list_library_roots
from app.db import ensure_dirs, init_db, async_session_maker
from app.routers.api import router as api_router
from app.ws_manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Roomz", description="LAN-synchronized single-stream audio")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Log all HTTP errors (4xx INFO, 5xx ERROR) and return the same response."""
    status = exc.status_code
    if status >= 500:
        logger.error(
            "HTTP %s %s %s: %s",
            request.method,
            request.url.path,
            status,
            exc.detail,
            exc_info=False,
        )
    else:
        logger.info(
            "HTTP %s %s %s: %s",
            request.method,
            request.url.path,
            status,
            exc.detail,
        )
    return JSONResponse(status_code=status, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def uncaught_exception_handler(request: Request, exc: Exception):
    """Log uncaught exceptions and return 500 with generic message."""
    logger.exception(
        "Uncaught exception %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.on_event("startup")
async def startup() -> None:
    """Create data dirs and DB tables."""
    ensure_dirs()
    await init_db()
    logger.info("Roomz started")


async def _resolve_track_path(track_id: int) -> tuple[Path, str]:
    """Resolve track by id; validate filepath under a library root or LIBRARY_BASE. Return (path, filename)."""
    async with async_session_maker() as session:
        track = await get_track_by_id(session, track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        path = Path(track.filepath).resolve()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        try:
            if path.is_relative_to(LIBRARY_BASE):
                return path, track.filename
        except (ValueError, TypeError):
            pass
        roots = await list_library_roots(session)
        for r in roots:
            try:
                if path.is_relative_to(Path(r.path).resolve()):
                    return path, track.filename
            except (ValueError, TypeError):
                continue
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/music/track/{track_id}")
async def serve_music_by_track(request: Request, track_id: int):
    """Serve audio file by track id with Range, Accept-Ranges, and ETag support."""
    path, filename = await _resolve_track_path(track_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    stat = path.stat()
    size = stat.st_size
    mtime = stat.st_mtime
    etag = f'"{int(mtime)}-{size}"'
    content_type = get_mime_for_filename(filename)

    range_header = request.headers.get("range")
    if not range_header or not range_header.strip().lower().startswith("bytes="):
        # Full file
        return FileResponse(
            path,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "ETag": etag,
            },
        )

    # Parse Range: bytes=start-end
    try:
        range_spec = range_header.strip()[6:]
        if "-" not in range_spec:
            raise ValueError("Invalid range")
        start_s, end_s = range_spec.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
        if start > end or start < 0:
            raise ValueError("Invalid range")
        end = min(end, size - 1)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=416, detail="Range not satisfiable") from e

    content_length = end - start + 1

    async def stream_chunks():
        async with aiofiles.open(path, "rb") as f:
            await f.seek(start)
            remaining = content_length
            chunk_size = 64 * 1024
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                data = await f.read(to_read)
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        stream_chunks(),
        status_code=206,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(content_length),
            "ETag": etag,
        },
    )


async def _build_state_snapshot() -> dict:
    """Build state_snapshot dict from DB (playback state + playlist)."""
    async with async_session_maker() as session:
        state = await get_or_create_playback_state(session)
        playlist = await get_playlist_item_dicts(session)
        order = await get_ordered_track_ids(session)
        current_track_filename = None
        if state.current_track_id:
            track = await get_track_by_id(session, state.current_track_id)
            if track:
                current_track_filename = track.filename
        ts = state.updated_at.timestamp() if state.updated_at else 0.0
        return {
            "type": "state_snapshot",
            "state": {
                "current_track_id": state.current_track_id,
                "is_playing": state.is_playing,
                "position_seconds": state.position_seconds,
                "last_update_server_timestamp": ts,
                "playlist_order": order,
            },
            "playlist": playlist,
            "current_track_filename": current_track_filename,
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Single global WebSocket: state sync, play/pause/seek/track, chat, playlist. Uses a new DB session per message (see get_db in db.py for request-scoped API usage)."""
    await manager.connect(websocket)
    try:
        snapshot = await _build_state_snapshot()
        await manager.send_to(websocket, snapshot)
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            typ = msg.get("type") or ""
            if typ == "sync":
                client_time = msg.get("client_time")
                if client_time is not None:
                    await manager.send_to(
                        websocket,
                        {
                            "type": "sync_response",
                            "server_utc": server_timestamp_utc(),
                            "client_time": client_time,
                        },
                    )
                continue
            if typ == "chat":
                author = msg.get("author") or "Anonymous"
                text = msg.get("text") or ""
                await manager.broadcast(
                    {
                        "type": "chat",
                        "author": author,
                        "text": text,
                        "timestamp": server_timestamp_utc(),
                    }
                )
                continue
            # Playback and playlist updates need DB + broadcast
            async with async_session_maker() as session:
                try:
                    if typ == "play":
                        await set_playback_state(session, is_playing=True)
                        await session.commit()
                        state = await get_or_create_playback_state(session)
                        ts = server_timestamp_utc()
                        await manager.broadcast(
                            {
                                "type": "play",
                                "position": state.position_seconds,
                                "server_timestamp": ts,
                                "track_id": state.current_track_id,
                                "is_playing": True,
                            }
                        )
                    elif typ == "pause":
                        await set_playback_state(session, is_playing=False)
                        await session.commit()
                        state = await get_or_create_playback_state(session)
                        ts = server_timestamp_utc()
                        await manager.broadcast(
                            {
                                "type": "pause",
                                "position": state.position_seconds,
                                "server_timestamp": ts,
                                "track_id": state.current_track_id,
                                "is_playing": False,
                            }
                        )
                    elif typ == "seek":
                        pos = msg.get("position_seconds", 0)
                        await set_playback_state(session, position_seconds=pos)
                        await session.commit()
                        state = await get_or_create_playback_state(session)
                        ts = server_timestamp_utc()
                        await manager.broadcast(
                            {
                                "type": "seek",
                                "position": state.position_seconds,
                                "server_timestamp": ts,
                                "track_id": state.current_track_id,
                            }
                        )
                    elif typ == "set_track":
                        track_id = msg.get("track_id")
                        if track_id is not None:
                            await set_playback_state(
                                session,
                                current_track_id=track_id,
                                position_seconds=0,
                                is_playing=True,
                            )
                            await session.commit()
                            state = await get_or_create_playback_state(session)
                            ts = server_timestamp_utc()
                            await manager.broadcast(
                                {
                                    "type": "set_track",
                                    "position": 0,
                                    "server_timestamp": ts,
                                    "track_id": track_id,
                                    "is_playing": True,
                                }
                            )
                    elif typ == "playlist_reorder":
                        order = msg.get("order")
                        if isinstance(order, list):
                            await set_playlist_order(session, order)
                            await session.commit()
                            playlist = await get_playlist_item_dicts(session)
                            await manager.broadcast(
                                {"type": "playlist_updated", "playlist": playlist}
                            )
                    elif typ == "playlist_add":
                        track_id = msg.get("track_id")
                        if track_id is not None:
                            await add_track_to_playlist(session, track_id)
                            await session.commit()
                            playlist = await get_playlist_item_dicts(session)
                            await manager.broadcast(
                                {"type": "playlist_updated", "playlist": playlist}
                            )
                    elif typ == "playlist_remove":
                        track_id = msg.get("track_id")
                        if track_id is not None:
                            await remove_track_from_playlist(session, track_id)
                            await session.commit()
                            playlist = await get_playlist_item_dicts(session)
                            await manager.broadcast(
                                {"type": "playlist_updated", "playlist": playlist}
                            )
                except Exception as e:
                    logger.exception("WS handler error: %s", e)
                    await session.rollback()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# API (WebSocket is above; mount API so /api/* is handled)
app.include_router(api_router)

# SPA: mount static last so / serves frontend and unknown paths fall back to index.html
frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
else:
    logger.warning("frontend/ not found; create frontend/index.html for SPA")
