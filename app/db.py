"""Async database engine, session factory, and dependency.

Ensures data/ and data/music/ exist on startup.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

# Path relative to project root (where uvicorn is typically run from)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MUSIC_DIR = DATA_DIR / "music"
DB_PATH = DATA_DIR / "app.db"


def ensure_dirs() -> None:
    """Create data/ and data/music/ if they do not exist."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)


DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency that yields an async session. Used by request-scoped API routes (commit on success, rollback on exception). WebSocket handler in main.py uses async_session_maker() per message for explicit commit boundaries."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create tables if they do not exist. Call on app startup."""
    ensure_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
