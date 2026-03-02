"""REST API: upload, scan, playlist CRUD."""

import logging
import re
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio_utils import ALLOWED_EXTENSIONS, extract_metadata
from app.crud import (
    add_track_to_playlist,
    create_track,
    get_playlist_entries_with_tracks,
    get_track_by_filename,
    get_track_by_id,
    remove_track_from_playlist,
    set_playlist_order,
)
from app.db import get_db, MUSIC_DIR
from app.schemas import (
    PlaylistAddItem,
    PlaylistReorder,
    PlaylistItem,
    ScanResult,
    TrackOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# Simple in-memory rate limit for uploads: max N per minute per... we don't have user, so global.
_upload_count = 0
_upload_window_start: float = 0
UPLOAD_RATE_LIMIT = 30  # max 30 uploads per minute


def _check_upload_rate_limit() -> None:
    import time

    global _upload_count, _upload_window_start
    now = time.time()
    if now - _upload_window_start > 60:
        _upload_count = 0
        _upload_window_start = now
    _upload_count += 1
    if _upload_count > UPLOAD_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded")


def _sanitize_filename(name: str) -> str:
    """Return a safe filename (no path traversal, only allowed chars)."""
    name = Path(name).name
    name = re.sub(r"[^\w\s\-\.]", "", name, flags=re.IGNORECASE)
    return name or "unnamed"


@router.post("/upload", response_model=TrackOut)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload an audio file to data/music/ and add to playlist."""
    _check_upload_rate_limit()
    filename = _sanitize_filename(file.filename or "unnamed")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )
    content_type = file.content_type or ""
    if (
        content_type
        and not content_type.startswith("audio/")
        and content_type != "application/octet-stream"
    ):
        raise HTTPException(status_code=400, detail="File must be audio")

    filepath = MUSIC_DIR / filename
    if filepath.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    try:
        content = await file.read()
    except Exception as e:
        logger.exception("Upload read failed")
        raise HTTPException(status_code=500, detail="Failed to read upload") from e

    try:
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(content)
    except Exception as e:
        logger.exception("Upload write failed for %s", filename)
        raise HTTPException(status_code=500, detail="Failed to save file") from e

    metadata = await extract_metadata(filepath)
    track = await create_track(
        session=db,
        filename=filename,
        title=metadata.get("title"),
        artist=metadata.get("artist"),
        album=metadata.get("album"),
        duration_seconds=metadata.get("duration_seconds"),
    )
    await add_track_to_playlist(db, track.id)
    return TrackOut(
        id=track.id,
        filename=track.filename,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        created_at=track.created_at,
    )


@router.post("/scan", response_model=ScanResult)
async def scan_folder(db: AsyncSession = Depends(get_db)):
    """Scan data/music/ and add any new audio files to DB and playlist."""
    added = 0
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for path in MUSIC_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        filename = path.name
        existing = await get_track_by_filename(db, filename)
        if existing is not None:
            continue
        metadata = await extract_metadata(path)
        track = await create_track(
            session=db,
            filename=filename,
            title=metadata.get("title"),
            artist=metadata.get("artist"),
            album=metadata.get("album"),
            duration_seconds=metadata.get("duration_seconds"),
        )
        await add_track_to_playlist(db, track.id)
        added += 1
    return ScanResult(added=added)


@router.get("/playlist", response_model=list[PlaylistItem])
async def get_playlist(db: AsyncSession = Depends(get_db)):
    """Return ordered playlist with track details."""
    entries = await get_playlist_entries_with_tracks(db)
    return [
        PlaylistItem(
            id=po.id,
            track_id=po.track_id,
            position=po.position,
            filename=t.filename,
            title=t.title,
            artist=t.artist,
            album=t.album,
            duration_seconds=t.duration_seconds,
        )
        for po, t in entries
    ]


@router.post("/playlist/items", response_model=PlaylistItem)
async def add_playlist_item(
    body: PlaylistAddItem,
    db: AsyncSession = Depends(get_db),
):
    """Add a track to the end of the playlist."""
    track = await get_track_by_id(db, body.track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    await add_track_to_playlist(db, body.track_id)
    entries = await get_playlist_entries_with_tracks(db)
    for po, t in entries:
        if t.id == body.track_id:
            return PlaylistItem(
                id=po.id,
                track_id=po.track_id,
                position=po.position,
                filename=t.filename,
                title=t.title,
                artist=t.artist,
                album=t.album,
                duration_seconds=t.duration_seconds,
            )
    raise HTTPException(status_code=500, detail="Failed to return added item")


@router.delete("/playlist/items/{track_id}")
async def remove_playlist_item(
    track_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove a track from the playlist by track_id."""
    await remove_track_from_playlist(db, track_id)
    return {"ok": True}


@router.put("/playlist/reorder")
async def reorder_playlist(
    body: PlaylistReorder,
    db: AsyncSession = Depends(get_db),
):
    """Replace playlist order with the given list of track_ids."""
    await set_playlist_order(db, body.order)
    return {"ok": True}
