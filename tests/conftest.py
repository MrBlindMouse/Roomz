"""Pytest fixtures for Roomz API and app tests.

All DB-reliant tests use a single in-memory SQLite DB per test. No tests touch
production data/app.db. The client fixture sets app.db test overrides and enters
TestClient(app) as a context manager so the app lifespan runs and init_db() creates
tables on the test DB. Use `client` for HTTP/API tests and `db_session` for direct
CRUD/player tests (same test DB). Optional: `test_engine` and `test_async_session_maker`
for tests that need an explicit DB handle without going through the app.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db as db_module
import app.main as main_module
from app.main import app
from app.models import Base

# Plain in-memory for optional test_engine (independent DB per test).
TEST_DATABASE_URL_MEMORY = "sqlite+aiosqlite:///:memory:"


def _create_test_tables(engine: object) -> None:
    """Create all tables on the test engine (sync wrapper for async create_all)."""
    async def _run() -> None:
        async with engine.begin() as conn:  # type: ignore[union-attr]
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_run())


@pytest.fixture
def test_engine():
    """Temporary in-memory SQLite engine with all tables. Use when you need an explicit test DB handle."""
    engine = create_async_engine(
        TEST_DATABASE_URL_MEMORY,
        echo=False,
        connect_args={"timeout": 5},
    )
    _create_test_tables(engine)
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def test_async_session_maker(test_engine):
    """Async session factory for the test DB. Use for tests that need a session without going through the app."""
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


@pytest.fixture
def client() -> TestClient:
    """HTTP client for the FastAPI app. Sets app.db test overrides (in-memory + StaticPool), enters TestClient so lifespan runs and init_db() creates tables."""
    engine = create_async_engine(
        TEST_DATABASE_URL_MEMORY,
        echo=False,
        connect_args={"timeout": 5},
        poolclass=StaticPool,
    )
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    db_module._test_engine = engine
    db_module._test_session_maker = session_maker
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        db_module._test_engine = None
        db_module._test_session_maker = None
        asyncio.run(engine.dispose())


@pytest.fixture
async def db_session(client: TestClient) -> AsyncSession:
    """Async DB session on the test DB. Commit in the test to make data visible to API."""
    session_maker = db_module._session_maker_for_request()
    async with session_maker() as session:
        yield session
