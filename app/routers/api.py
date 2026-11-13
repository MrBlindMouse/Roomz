"""REST API: library listing, upload, and MPD rescan."""

import logging
import re
import time
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.art import load_album_art, resolve_library_file
from app.config import library_base
from app.mpd_access import get_mpd
from app.mpd_client import MpdClient, MpdError, log_mpd_error
from app.schemas import LibraryOut, LibraryTrack, ScanResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}

# Global upload cap. There are no users; the limit is for the whole process.
_upload_count = 0
_upload_window_start: float = 0.0
UPLOAD_RATE_LIMIT = 30


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
    """Return a safe filename (no path traversal)."""
    name = Path(name).name
    name = re.sub(r"[^\w\s\-\.]", "", name, flags=re.IGNORECASE)
    return name or "unnamed"


def _mpd_error(exc: MpdError) -> HTTPException:
    log_mpd_error(logger, exc, "MPD API")
    return HTTPException(status_code=503, detail=str(exc))


def _upload_subdir(raw: str, root: Path) -> tuple[Path, str]:
    """Directory under the music root, and the MPD path prefix for that directory."""
    text = raw.strip().replace("\\", "/").strip("/")
    if not text:
        return root, ""
    rel = Path(text)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise HTTPException(status_code=400, detail="Invalid folder")
    dest = (root / rel).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise HTTPException(status_code=400, detail="Invalid folder")
    return dest, rel.as_posix()


@router.get("/art")
async def get_art(file: str = Query(..., min_length=1), mpd: MpdClient = Depends(get_mpd)):
    """Album cover for a library path: MPD embedded art, then folder cover image."""
    try:
        resolve_library_file(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file") from exc
    try:
        loaded = await load_album_art(mpd, file)
    except MpdError as exc:
        raise _mpd_error(exc) from exc
    if loaded is None:
        raise HTTPException(status_code=404, detail="No artwork")
    data, media_type = loaded
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/library", response_model=LibraryOut)
async def get_library(mpd: MpdClient = Depends(get_mpd)):
    """List files MPD has indexed under the music directory."""
    try:
        tracks = await mpd.list_library()
    except MpdError as exc:
        raise _mpd_error(exc) from exc
    return LibraryOut(
        music_directory=str(library_base()),
        tracks=[LibraryTrack.model_validate(track) for track in tracks],
    )


@router.post("/scan", response_model=ScanResult)
async def scan_folder(mpd: MpdClient = Depends(get_mpd)):
    """Rescan the music directory. MPD is the library index."""
    try:
        if await mpd.is_updating():
            raise HTTPException(status_code=409, detail="Scan already in progress")
        await mpd.update_library()
    except HTTPException:
        raise
    except MpdError as exc:
        raise _mpd_error(exc) from exc
    return ScanResult(ok=True)


@router.post("/upload", response_model=LibraryTrack)
async def upload_file(
    file: UploadFile = File(...),
    folder: str = Form(""),
    mpd: MpdClient = Depends(get_mpd),
):
    """Save an audio file into the music directory, rescan, and queue it."""
    _check_upload_rate_limit()
    filename = _sanitize_filename(file.filename or "unnamed")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    content_type = file.content_type or ""
    if (
        content_type
        and not content_type.startswith("audio/")
        and content_type != "application/octet-stream"
    ):
        raise HTTPException(status_code=400, detail="File must be audio")

    root = library_base()
    directory, prefix = _upload_subdir(folder, root)
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / filename
    stored = f"{prefix}/{filename}" if prefix else filename
    if filepath.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    try:
        content = await file.read()
    except Exception as exc:
        logger.exception("Upload read failed")
        raise HTTPException(status_code=500, detail="Failed to read upload") from exc

    try:
        async with aiofiles.open(filepath, "wb") as handle:
            await handle.write(content)
    except Exception as exc:
        logger.exception("Upload write failed for %s", filename)
        raise HTTPException(status_code=500, detail="Failed to save file") from exc

    try:
        await mpd.update_library()
        await mpd.add(stored)
    except MpdError as exc:
        raise _mpd_error(exc) from exc
    return LibraryTrack(file=stored, title=filename, folder=prefix)
