"""REST API: library roots, upload, scan, playlist CRUD."""

import asyncio
import logging
import re
import time
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio_utils import ALLOWED_EXTENSIONS, extract_metadata
from app.config import validate_library_path
from app.crud import (
    add_track_to_playlist,
    create_library_root,
    create_track,
    delete_library_root,
    get_default_library_root,
    get_library_root_by_id,
    get_playlist_item_dicts,
    get_track_by_id,
    get_track_by_filepath,
    list_all_tracks,
    list_library_roots,
    playlist_entry_to_item_dict,
    remove_track_from_playlist,
    set_playlist_order,
    track_folder_relative,
)
from app.db import get_db
from app.schemas import (
    LibraryRootCreate,
    LibraryRootOut,
    PlaylistAddItem,
    PlaylistItem,
    PlaylistReorder,
    ScanResult,
    TrackOut,
    TrackWithRootOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# Simple in-memory rate limit for uploads: max N per minute per... we don't have user, so global.
_upload_count = 0
_upload_window_start: float = 0
UPLOAD_RATE_LIMIT = 30  # max 30 uploads per minute


def _check_upload_rate_limit() -> None:
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


# ----- All tracks (for Library browse) -----


@router.get("/tracks", response_model=list[TrackWithRootOut])
async def get_all_tracks(db: AsyncSession = Depends(get_db)):
    """List all tracks in the library with library root info for grouping."""
    tracks = await list_all_tracks(db)
    roots = await list_library_roots(db)
    root_map = {r.id: (r.name or r.path) for r in roots}
    root_path_map = {r.id: r.path for r in roots}
    return [
        TrackWithRootOut(
            id=t.id,
            filename=t.filename,
            title=t.title,
            artist=t.artist,
            album=t.album,
            duration_seconds=t.duration_seconds,
            created_at=t.created_at,
            library_root_id=t.library_root_id,
            library_root_name=root_map.get(t.library_root_id) if t.library_root_id else None,
            folder=track_folder_relative(t.filepath, t.library_root_id, root_path_map),
        )
        for t in tracks
    ]


# ----- Library roots -----


@router.get("/library-roots", response_model=list[LibraryRootOut])
async def get_library_roots_list(db: AsyncSession = Depends(get_db)):
    """List all library roots."""
    roots = await list_library_roots(db)
    return [
        LibraryRootOut(
            id=r.id,
            path=r.path,
            name=r.name,
            created_at=r.created_at,
        )
        for r in roots
    ]


@router.post("/library-roots", response_model=LibraryRootOut)
async def add_library_root(
    body: LibraryRootCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a library folder. Path must be under allowed base."""
    resolved = validate_library_path(body.path)
    path_str = str(resolved)
    roots = await list_library_roots(db)
    if any(r.path == path_str for r in roots):
        raise HTTPException(status_code=409, detail="Path already added")
    root = await create_library_root(db, path=path_str, name=body.name)
    return LibraryRootOut(
        id=root.id,
        path=root.path,
        name=root.name,
        created_at=root.created_at,
    )


@router.delete("/library-roots/{root_id}")
async def remove_library_root(
    root_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove a library root. Fails if any tracks reference it."""
    root = await get_library_root_by_id(db, root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Library root not found")
    from sqlalchemy import func, select

    from app.models import Track

    result = await db.execute(
        select(func.count()).select_from(Track).where(Track.library_root_id == root_id)
    )
    if (result.scalar() or 0) > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove: folder has tracks. Remove tracks from playlist first.",
        )
    await delete_library_root(db, root_id)
    return {"ok": True}


@router.post("/upload", response_model=TrackOut)
async def upload_file(
    file: UploadFile = File(...),
    root_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Upload an audio file to the chosen library root (or default) and add to playlist."""
    _check_upload_rate_limit()
    root = (
        await get_library_root_by_id(db, root_id)
        if root_id is not None
        else await get_default_library_root(db)
    )
    if root is None:
        raise HTTPException(
            status_code=400,
            detail="No library root. Add a folder in Library first.",
        )
    root_path = Path(root.path)
    root_path.mkdir(parents=True, exist_ok=True)
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

    filepath = root_path / filename
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
        filepath=str(filepath.resolve()),
        library_root_id=root.id,
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


def _collect_audio_paths_recursive(root_path: Path) -> list[Path]:
    """Recursively collect all audio files under root_path (sync, for executor)."""
    out = []
    for p in root_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            out.append(p)
    return out


# Commit every N new tracks during scan to release DB lock so other requests (playlist, playback) can proceed.
SCAN_COMMIT_BATCH_SIZE = 50

# Only one scan at a time; concurrent request gets 409.
_scan_in_progress = False


@router.post("/scan", response_model=ScanResult)
async def scan_folder(
    root_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Scan library root(s) recursively and add new audio files to DB and playlist."""
    global _scan_in_progress
    if _scan_in_progress:
        raise HTTPException(status_code=409, detail="Scan already in progress")
    _scan_in_progress = True
    try:
        if root_id is not None:
            roots = [await get_library_root_by_id(db, root_id)]
            if roots[0] is None:
                raise HTTPException(status_code=404, detail="Library root not found")
        else:
            roots = await list_library_roots(db)
        added = 0
        loop = asyncio.get_event_loop()
        for root in roots:
            root_path = Path(root.path)
            if not root_path.exists() or not root_path.is_dir():
                continue
            paths = await loop.run_in_executor(None, _collect_audio_paths_recursive, root_path)
            for path in paths:
                filepath_str = str(path.resolve())
                existing = await get_track_by_filepath(db, root.id, filepath_str)
                if existing is not None:
                    continue
                metadata = await extract_metadata(path)
                track = await create_track(
                    session=db,
                    filename=path.name,
                    filepath=filepath_str,
                    library_root_id=root.id,
                    title=metadata.get("title"),
                    artist=metadata.get("artist"),
                    album=metadata.get("album"),
                    duration_seconds=metadata.get("duration_seconds"),
                )
                await add_track_to_playlist(db, track.id)
                added += 1
                if added % SCAN_COMMIT_BATCH_SIZE == 0:
                    await db.commit()
        return ScanResult(added=added)
    finally:
        _scan_in_progress = False


@router.get("/playlist", response_model=list[PlaylistItem])
async def get_playlist(db: AsyncSession = Depends(get_db)):
    """Return ordered playlist with track details."""
    dicts = await get_playlist_item_dicts(db)
    return [PlaylistItem.model_validate(d) for d in dicts]


@router.post("/playlist/items", response_model=PlaylistItem)
async def add_playlist_item(
    body: PlaylistAddItem,
    db: AsyncSession = Depends(get_db),
):
    """Add a track to the end of the playlist."""
    track = await get_track_by_id(db, body.track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    added = await add_track_to_playlist(db, body.track_id)
    if added is not None:
        po, t = added
        roots = await list_library_roots(db)
        root_path_map = {r.id: r.path for r in roots}
        return PlaylistItem.model_validate(
            playlist_entry_to_item_dict(po, t, root_path_map)
        )
    # Already in playlist: return the existing item.
    dicts = await get_playlist_item_dicts(db)
    for d in dicts:
        if d["track_id"] == body.track_id:
            return PlaylistItem.model_validate(d)
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
