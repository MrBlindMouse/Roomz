"""FastAPI app: MPD control, timestamped PCM fanout, SPA."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    ensure_dirs,
    fifo_path,
    mpd_config_problems,
    mpd_host,
    mpd_port,
    server_timestamp_utc,
)
from app.logging_config import configure_logging
from app.mpd_access import mpd_from_app
from app.mpd_client import MpdClient, MpdError, empty_status, probe_mpd
from app.playlists import (
    active_name,
    clear_active_name,
    folder_uri,
    playlist_name,
    set_active_name,
)
from app.routers.api import router as api_router
from app.stream import fifo_is_ready, supervise_fifo
from app.ws_manager import manager

configure_logging()
logger = logging.getLogger(__name__)


async def _publish_status() -> None:
    """Broadcast MPD status. Audio sync does not use this message."""
    client: MpdClient | None = getattr(app.state, "mpd", None)
    if client is None:
        return
    try:
        payload = await client.snapshot()
    except Exception:
        logger.exception("MPD status")
        return
    await manager.broadcast(payload)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect the MPD watcher and the FIFO reader. MPD itself is not started here."""
    ensure_dirs()
    for problem in mpd_config_problems():
        logger.error("MPD config: %s", problem)
    stop = asyncio.Event()
    client = MpdClient(mpd_host(), mpd_port())
    app.state.mpd = client
    idle_task = asyncio.create_task(client.supervise_idle(_publish_status, stop))
    fifo_task = asyncio.create_task(supervise_fifo(fifo_path(), manager.broadcast_bytes, stop))
    logger.info("Roomz started")
    try:
        yield
    finally:
        stop.set()
        idle_task.cancel()
        fifo_task.cancel()
        await client.close()
        for task in (idle_task, fifo_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Roomz", description="LAN-synchronized single-stream audio", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    """200 only when mpd.conf is usable, MPD answers, and the FIFO exists."""
    host, port = mpd_host(), mpd_port()
    fifo = fifo_path()
    path_problems = mpd_config_problems()
    problems = list(path_problems)
    mpd_ok = await probe_mpd(host, port)
    fifo_ok = fifo_is_ready(fifo)
    if not mpd_ok:
        problems.append(f"MPD is not accepting connections at {host}:{port}.")
    if not fifo_ok:
        problems.append(
            f"PCM FIFO is missing at {fifo}. MPD's fifo output creates it when MPD starts."
        )
    ok = not problems
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "unhealthy",
            "mpd": "ok" if mpd_ok else "down",
            "fifo": "ok" if fifo_ok else "down",
            "paths": "ok" if not path_problems else "mismatch",
            "problems": problems,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Log HTTP errors (4xx INFO, 5xx ERROR) and return the same response."""
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
    """Log uncaught exceptions and return 500 with a generic message."""
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


_QUEUE_EDITS = frozenset(
    {"playlist_add", "playlist_remove", "playlist_clear", "playlist_reorder", "play_uri"}
)


async def _autosave(mpd: MpdClient) -> None:
    """Write the live queue back to the named playlist, when one is selected."""
    name = active_name()
    if name:
        await mpd.store_queue_as(name)


async def _run_command(msg: dict) -> bool:
    """Apply one control message. Return False when the type is not a command."""
    mpd = mpd_from_app(app)
    typ = msg.get("type") or ""
    if typ == "playlist_new":
        name = playlist_name(msg.get("name"))
        set_active_name(name)
        await mpd.store_queue_as(name)
        return True
    if typ == "playlist_switch":
        name = playlist_name(msg.get("name"))
        await mpd.load_stored(name)
        set_active_name(name)
        return True
    if typ == "playlist_detach":
        clear_active_name()
        return True
    if typ == "playlist_delete":
        name = playlist_name(msg.get("name"))
        await mpd.delete_stored(name)
        if active_name() == name:
            clear_active_name()
        return True
    if typ == "play_folder":
        clear_active_name()
        await mpd.play_directory(folder_uri(msg.get("uri")))
        return True
    if typ == "play":
        await mpd.play()
    elif typ == "pause":
        await mpd.pause()
    elif typ == "seek":
        await mpd.seek(float(msg.get("position_seconds") or 0))
    elif typ == "next":
        await mpd.next_track()
    elif typ == "previous":
        await mpd.previous_track()
    elif typ == "random":
        await mpd.set_random(bool(msg.get("on")))
    elif typ == "repeat":
        await mpd.set_repeat_mode(int(msg.get("mode") or 0))
    elif typ == "playlist_add":
        uri = msg.get("uri")
        if isinstance(uri, str) and uri:
            await mpd.add(uri)
    elif typ == "playlist_remove":
        song_id = msg.get("id")
        if song_id is not None:
            await mpd.deleteid(int(song_id))
    elif typ == "playlist_clear":
        await mpd.clear()
    elif typ == "play_id":
        song_id = msg.get("id")
        if song_id is not None:
            await mpd.playid(int(song_id))
    elif typ == "play_uri":
        uri = msg.get("uri")
        if isinstance(uri, str) and uri:
            await mpd.play_uri(uri)
    elif typ == "playlist_reorder":
        order = msg.get("order")
        if isinstance(order, list):
            await mpd.reorder([int(song_id) for song_id in order])
    else:
        return False
    if typ in _QUEUE_EDITS:
        await _autosave(mpd)
    return True


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Control channel plus binary PCM frames on the same socket."""
    await manager.connect(websocket)
    try:
        try:
            snapshot = await mpd_from_app(app).snapshot()
        except Exception:
            logger.exception("initial MPD snapshot")
            snapshot = empty_status()
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
            try:
                handled = await _run_command(msg)
            except MpdError as exc:
                await manager.send_to(websocket, {"type": "error", "detail": str(exc)})
                continue
            except Exception:
                logger.exception("MPD command failed")
                await manager.send_to(websocket, {"type": "error", "detail": "MPD command failed"})
                continue
            if not handled:
                continue
            try:
                await manager.broadcast(await mpd_from_app(app).snapshot())
            except Exception:
                logger.exception("MPD status after command")
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


app.include_router(api_router)

frontend_path = Path(__file__).resolve().parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
else:
    logger.warning("frontend/ not found; create frontend/index.html for SPA")
