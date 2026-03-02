"""Extract audio metadata (title, artist, album, duration) using mutagen."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}


def get_mime_for_filename(filename: str) -> str:
    """Return MIME type for filename by extension. Defaults to application/octet-stream."""
    ext = Path(filename).suffix.lower()
    return MIME_MAP.get(ext, "application/octet-stream")


def _extract_metadata_sync(filepath: Path) -> dict[str, Any]:
    """Synchronous metadata extraction (mutagen is sync). Call from thread."""
    out: dict[str, Any] = {
        "title": None,
        "artist": None,
        "album": None,
        "duration_seconds": None,
    }
    try:
        audio = MutagenFile(str(filepath))
        if audio is None:
            return out
        if hasattr(audio, "info") and audio.info is not None and hasattr(audio.info, "length"):
            out["duration_seconds"] = float(audio.info.length)
        tags = getattr(audio, "tags", None)
        if tags is not None:
            for key, pydantic_key in [("title", "title"), ("artist", "artist"), ("album", "album")]:
                try:
                    val = tags.get(key)
                    if val is not None:
                        out[pydantic_key] = (
                            str(val[0]) if isinstance(val, (list, tuple)) else str(val)
                        )
                except Exception:
                    pass
        if out["title"] is None and filepath.stem:
            out["title"] = filepath.stem
    except Exception as e:
        logger.warning("Failed to extract metadata for %s: %s", filepath, e)
    return out


async def extract_metadata(filepath: Path) -> dict[str, Any]:
    """Extract title, artist, album, duration from an audio file. Async-safe (runs in executor)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract_metadata_sync, filepath)
