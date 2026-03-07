"""Pytest fixtures for Roomz API and app tests. Uses in-memory SQLite for isolation."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db as db_module
import app.main as main_module
from app.main import app
from app.models import Base


def _create_test_tables(engine: object) -> None:
    """Create all tables on the test engine (sync wrapper for async create_all)."""
    async def _run() -> None:
        async with engine.begin() as conn:  # type: ignore[union-attr]
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_run())


@pytest.fixture
def client() -> TestClient:
    """HTTP client for the FastAPI app. Patches DB to in-memory SQLite before first request."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"timeout": 5},
    )
    _create_test_tables(test_engine)
    test_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    # main.py imports async_session_maker at load time; init_db uses db_module.engine
    orig_engine = db_module.engine
    orig_maker = main_module.async_session_maker
    db_module.engine = test_engine
    main_module.async_session_maker = test_session_maker
    try:
        yield TestClient(app)
    finally:
        db_module.engine = orig_engine
        main_module.async_session_maker = orig_maker


@pytest.fixture
async def db_session(client: TestClient) -> AsyncSession:
    """Yield an async DB session using the test DB. Commit in the test to make data visible to API."""
    client.get("/health")  # trigger startup so test DB is initialized
    async with main_module.async_session_maker() as session:
        yield session
