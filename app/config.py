"""Library path config and validation. All library roots must be under LIBRARY_BASE."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException

from app.db import DATA_DIR

load_dotenv()

# All library root paths must resolve under this directory (env or default data/)
LIBRARY_BASE_STR = os.environ.get("ROOMZ_LIBRARY_BASE")
LIBRARY_BASE: Path = Path(LIBRARY_BASE_STR).resolve() if LIBRARY_BASE_STR else (DATA_DIR.resolve())


def validate_library_path(path: str) -> Path:
    """Resolve path to absolute, ensure it is a directory under LIBRARY_BASE. Raise 400 if invalid."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail="Invalid path") from e
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    if not resolved.exists():
        raise HTTPException(status_code=400, detail="Path does not exist")
    try:
        if not resolved.is_relative_to(LIBRARY_BASE):
            raise HTTPException(
                status_code=400,
                detail="Path must be under the allowed library base",
            )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Path must be under the allowed library base",
        )
    return resolved
