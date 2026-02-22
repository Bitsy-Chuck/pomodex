"""Shared test fixtures: test Postgres, async DB session, FastAPI test client."""

import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer

# Postgres container — started once per session
_pg_container = None

# Mock unavailable GCP packages so gcp_iam.py can be imported in tests
# (the actual GCP calls are mocked per-test via unittest.mock.patch)
for _mod in [
    "google.cloud.iam_admin_v1",
    "google.cloud.storage",
    "google.cloud",
    "google.oauth2.service_account",
    "google.oauth2",
    "google.api_core.exceptions",
    "google.api_core",
    "google",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


def pytest_configure(config):
    """Start Postgres container once for the entire test session."""
    global _pg_container
    _pg_container = PostgresContainer("postgres:16-alpine", dbname="test_sandboxes", username="test", password="test")
    _pg_container.start()
    host = _pg_container.get_container_host_ip()
    port = _pg_container.get_exposed_port(5432)
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://test:test@{host}:{port}/test_sandboxes"
    )


def pytest_unconfigure(config):
    global _pg_container
    if _pg_container:
        _pg_container.stop()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Yield a clean DB session. Tables are recreated per-test."""
    # Import after DATABASE_URL is set
    from backend.project_service.models.database import Base, engine, async_session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db):
    """Async HTTP test client for the FastAPI app."""
    from backend.project_service.main import app
    from backend.project_service.models.database import get_db, async_session

    async def _override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client):
    """Register a user and return auth headers with a valid access token."""
    await client.post("/auth/register", json={
        "email": "testuser@example.com",
        "password": "SecurePass123!",
    })
    resp = await client.post("/auth/login", json={
        "email": "testuser@example.com",
        "password": "SecurePass123!",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
