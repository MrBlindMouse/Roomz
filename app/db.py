"""Async database engine, session factory, and dependency.

Ensures data/ and data/music/ exist on startup.
"""

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

logger = logging.getLogger(__name__)

# Path relative to project root (where uvicorn is typically run from)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MUSIC_DIR = DATA_DIR / "music"
DB_PATH = DATA_DIR / "app.db"


def ensure_dirs() -> None:
    """Create data/ and data/music/ if they do not exist."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)


DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Timeout (seconds) for SQLite to wait for lock before failing. Reduces "database is locked" during concurrent use.
SQLITE_TIMEOUT = 30

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"timeout": SQLITE_TIMEOUT},
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Test overrides: when set by tests, init_db and get_db use these instead (same process, any thread).
_test_engine = None
_test_session_maker = None


def _engine_for_init() -> Any:
    return _test_engine if _test_engine is not None else engine


def _session_maker_for_request():
    return _test_session_maker if _test_session_maker is not None else async_session_maker


async def get_db() -> AsyncSession:
    """Dependency that yields an async session. Used by request-scoped API routes (commit on success, rollback on exception). WebSocket handler in main.py uses async_session_maker() per message for explicit commit boundaries."""
    async with _session_maker_for_request()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.debug("Request failed, session rolled back")
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create tables if they do not exist. Call on app startup. Enables WAL for better concurrent read/write."""
    ensure_dirs()
    e = _engine_for_init()
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # WAL mode allows one writer and multiple readers; reduces lock contention during scan vs other requests.
    async with e.connect() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
