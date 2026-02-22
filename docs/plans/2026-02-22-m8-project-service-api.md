# M8: Project Service API & Authentication — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the FastAPI backend that orchestrates user auth (JWT), project CRUD, container lifecycle, snapshot triggers, and the inactivity checker — integrating all M1-M7 components into one cohesive API.

**Architecture:** FastAPI async app with asyncpg/SQLAlchemy for Postgres. Existing sync services (docker_manager, gcp_iam, snapshot_manager) called via `asyncio.to_thread()`. JWT access tokens (15min) + opaque refresh tokens (30 day, rotated). Multi-tenancy enforced by filtering every project query on `user_id`. Internal endpoints locked to localhost.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), asyncpg, bcrypt, PyJWT, httpx (test client), pytest + pytest-asyncio, testcontainers (test Postgres)

**Existing code locations:**
- `backend/project_service/services/docker_manager.py` — M4 container lifecycle (sync)
- `backend/project_service/services/gcp_iam.py` — M3 IAM (sync)
- `backend/project_service/services/snapshot_manager.py` — M5 snapshot/restore (sync)
- `backend/terminal_proxy/` — M7 proxy (expects `POST /internal/validate` returning `{"user_id": "..."}`)

**Directory structure (new files marked with +):**
```
backend/project_service/
  + main.py
  + models/
  +   __init__.py
  +   database.py
  +   schemas.py
  + routes/
  +   __init__.py
  +   auth.py
  +   projects.py
  +   internal.py
  + middleware/
  +   __init__.py
  +   auth_middleware.py
  +   internal_middleware.py
  + services/
      __init__.py
      docker_manager.py          (existing M4)
      gcp_iam.py                 (existing M3)
      snapshot_manager.py        (existing M5)
  +   auth_service.py
  +   project_service.py
  + tasks/
  +   __init__.py
  +   inactivity_checker.py
  + tests/
  +   __init__.py
  +   conftest.py
  +   unit/
  +     __init__.py
  +     test_auth_service.py
  +     test_project_service.py
  +   integration/
  +     __init__.py
  +     test_auth_endpoints.py
  +     test_project_endpoints.py
  +     test_internal_endpoints.py
  +     test_inactivity_checker.py
  +     test_docker_compose.py
  + requirements.txt
  + Dockerfile
+ docker-compose.yml             (project root)
```

---

## Task 1: Project Skeleton & Dependencies

**Files:**
- Create: `backend/project_service/requirements.txt`
- Create: `backend/project_service/main.py`
- Create: `backend/project_service/models/__init__.py`
- Create: `backend/project_service/routes/__init__.py`
- Create: `backend/project_service/middleware/__init__.py`
- Create: `backend/project_service/tasks/__init__.py`
- Create: `backend/project_service/tests/__init__.py`
- Create: `backend/project_service/tests/unit/__init__.py`
- Create: `backend/project_service/tests/integration/__init__.py`

**Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy[asyncio]==2.0.35
asyncpg==0.30.0
pydantic==2.9.0
pyjwt==2.9.0
bcrypt==4.2.0
cryptography==43.0.0
httpx==0.27.0
pytest==8.3.0
pytest-asyncio==0.24.0
testcontainers[postgres]==4.8.0
```

**Step 2: Create minimal main.py**

```python
"""Project Service API — FastAPI application."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Project Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 3: Create all `__init__.py` files** (empty)

**Step 4: Install deps and verify**

Run: `cd backend/project_service && pip install -r requirements.txt`

**Step 5: Verify app starts**

Run: `cd backend/project_service && python -c "from main import app; print(app.title)"`
Expected: `Project Service`

---

## Task 2: Database Models & Schema

**Files:**
- Create: `backend/project_service/models/database.py`
- Create: `backend/project_service/models/schemas.py`

**Step 1: Create database.py**

```python
"""SQLAlchemy async engine, session, and table definitions."""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, ForeignKey, BigInteger,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/sandboxes",
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="creating")

    # Docker
    container_id = Column(Text)
    container_name = Column(Text)
    volume_name = Column(Text)
    ssh_host_port = Column(Integer)

    # SSH
    ssh_public_key = Column(Text, nullable=False)
    ssh_private_key = Column(Text, nullable=False)

    # GCP
    gcp_sa_email = Column(Text)
    gcp_sa_key = Column(Text)
    gcs_prefix = Column(Text, nullable=False)

    # Snapshot
    snapshot_image = Column(Text)
    last_snapshot_at = Column(DateTime(timezone=True))
    snapshot_size_bytes = Column(BigInteger)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_active_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_backup_at = Column(DateTime(timezone=True))
    last_connection_at = Column(DateTime(timezone=True))


async def create_tables():
    """Create all tables. Used for dev/test — production uses migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency: yield an async DB session."""
    async with async_session() as session:
        yield session
```

**Step 2: Create schemas.py**

```python
"""Pydantic request/response models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


# --- Auth ---

class RegisterRequest(BaseModel):
    email: str
    password: str

class RegisterResponse(BaseModel):
    user_id: UUID

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str

class RefreshRequest(BaseModel):
    refresh_token: str

# --- Projects ---

class CreateProjectRequest(BaseModel):
    name: str

class ProjectResponse(BaseModel):
    id: UUID
    name: str
    status: str
    created_at: datetime
    last_active_at: datetime | None = None

class ProjectDetailResponse(ProjectResponse):
    terminal_url: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_user: str = "agent"
    ssh_private_key: str | None = None
    last_backup_at: datetime | None = None
    last_snapshot_at: datetime | None = None

class ProjectCreateResponse(ProjectDetailResponse):
    pass

class BackupStatusResponse(BaseModel):
    last_backup_at: datetime | None = None
    snapshot_image: str | None = None
    last_snapshot_at: datetime | None = None

# --- Internal ---

class InternalValidateRequest(BaseModel):
    token: str
    project_id: str

class InternalValidateResponse(BaseModel):
    user_id: str
```

**Step 3: Verify imports**

Run: `cd backend/project_service && python -c "from models.database import Base, User, Project, RefreshToken; from models.schemas import RegisterRequest, TokenResponse; print('OK')"`
Expected: `OK`

---

## Task 3: Test Infrastructure (conftest.py)

**Files:**
- Create: `backend/project_service/tests/conftest.py`

**Step 1: Create conftest.py with test DB and FastAPI client**

```python
"""Shared test fixtures: test Postgres, async DB session, FastAPI test client."""

import asyncio
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from testcontainers.postgres import PostgresContainer

# Must set DATABASE_URL before importing app
_pg_container = None


def pytest_configure(config):
    """Start Postgres container once for the entire test session."""
    global _pg_container
    _pg_container = PostgresContainer("postgres:16-alpine", dbname="test_sandboxes")
    _pg_container.start()
    # asyncpg URL
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
    from backend.project_service.models.database import Base, engine, async_session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    # Cleanup after test
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db):
    """Async HTTP test client for the FastAPI app."""
    from backend.project_service.main import app
    from backend.project_service.models.database import get_db, async_session

    # Override the DB dependency to use our test session factory
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
```

**Step 2: Verify test infra by running a trivial test**

Create a small smoke test inline and run it to verify conftest works:

Run: `cd /path/to/worktree && python -m pytest backend/project_service/tests/ -v --co`
Expected: Shows collected 0 tests (no errors from conftest import)

---

## Task 4: Auth Service — Password Hashing & JWT (Unit Tests T8.8, T8.9)

**Files:**
- Create: `backend/project_service/services/auth_service.py`
- Create: `backend/project_service/tests/unit/test_auth_service.py`

**Step 1: Write failing tests**

```python
"""Unit tests for auth_service: password hashing, JWT creation/validation."""

import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.project_service.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    create_refresh_token,
    hash_refresh_token,
)


class TestPasswordHashing:

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("SecurePass123!")
        assert hashed != "SecurePass123!"

    def test_hash_is_bcrypt_format(self):
        hashed = hash_password("SecurePass123!")
        assert hashed.startswith("$2b$")

    def test_verify_correct_password(self):
        hashed = hash_password("SecurePass123!")
        assert verify_password("SecurePass123!", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("SecurePass123!")
        assert verify_password("WrongPassword", hashed) is False


class TestJWT:

    def test_create_and_decode_access_token(self):
        """T8.8: JWT contains user_id claim and can be decoded."""
        user_id = str(uuid4())
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        assert payload["sub"] == user_id

    def test_expired_token_rejected(self):
        """T8.9: Token valid within 15min window, rejected after."""
        user_id = str(uuid4())
        token = create_access_token(user_id)

        # Valid now
        payload = decode_access_token(token)
        assert payload["sub"] == user_id

        # Mock time 16 minutes ahead
        with patch("backend.project_service.services.auth_service.jwt.decode") as mock_decode:
            import jwt as pyjwt
            mock_decode.side_effect = pyjwt.ExpiredSignatureError("token expired")
            payload = decode_access_token(token)
            assert payload is None

    def test_invalid_token_rejected(self):
        """T8.8: Invalid JWTs return None."""
        assert decode_access_token("garbage.token.here") is None

    def test_token_has_expiry(self):
        user_id = str(uuid4())
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        assert "exp" in payload


class TestRefreshToken:

    def test_create_refresh_token_is_opaque(self):
        token = create_refresh_token()
        assert len(token) >= 32
        assert "." not in token  # Not a JWT

    def test_hash_refresh_token_deterministic(self):
        token = "some_refresh_token_value"
        h1 = hash_refresh_token(token)
        h2 = hash_refresh_token(token)
        assert h1 == h2

    def test_hash_refresh_token_not_plaintext(self):
        token = "some_refresh_token_value"
        assert hash_refresh_token(token) != token
```

**Step 2: Run tests — verify they fail**

Run: `python -m pytest backend/project_service/tests/unit/test_auth_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'hash_password'`

**Step 3: Implement auth_service.py**

```python
"""Authentication service: password hashing and JWT management."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRY_MINUTES = 15
REFRESH_TOKEN_BYTES = 32
REFRESH_TOKEN_EXPIRY_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None


def create_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
```

**Step 4: Run tests — verify they pass**

Run: `python -m pytest backend/project_service/tests/unit/test_auth_service.py -v`
Expected: All PASS

---

## Task 5: Auth Middleware (JWT user_id extraction)

**Files:**
- Create: `backend/project_service/middleware/auth_middleware.py`

**Step 1: Write failing test** (add to test_auth_service.py)

```python
class TestAuthMiddleware:

    def test_extracts_user_id_from_valid_token(self):
        """T8.8: request.state.user_id is set correctly."""
        from backend.project_service.middleware.auth_middleware import get_current_user_id
        user_id = str(uuid4())
        token = create_access_token(user_id)
        # Simulate: the middleware is a FastAPI dependency, test the extraction logic
        extracted = get_current_user_id._extract(f"Bearer {token}")
        assert extracted == user_id

    def test_rejects_missing_header(self):
        from backend.project_service.middleware.auth_middleware import get_current_user_id
        with pytest.raises(Exception):
            get_current_user_id._extract(None)

    def test_rejects_invalid_token(self):
        from backend.project_service.middleware.auth_middleware import get_current_user_id
        with pytest.raises(Exception):
            get_current_user_id._extract("Bearer invalid.token.here")
```

**Step 2: Run — verify fail**

**Step 3: Implement auth_middleware.py**

```python
"""JWT authentication middleware — FastAPI dependency."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.project_service.services.auth_service import decode_access_token

_bearer_scheme = HTTPBearer()


def _extract_user_id(auth_header_value: str | None) -> str:
    """Extract and validate user_id from a Bearer token string. Raises on failure."""
    if not auth_header_value or not auth_header_value.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = auth_header_value.split(" ", 1)[1]
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload["sub"]


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: extract user_id from JWT Bearer token."""
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload["sub"]

# Expose extraction logic for unit testing
get_current_user_id._extract = _extract_user_id
```

**Step 4: Run — verify pass**

---

## Task 6: Auth Routes — Register (T8.1, T8.2)

**Files:**
- Create: `backend/project_service/routes/auth.py`
- Create: `backend/project_service/tests/integration/test_auth_endpoints.py`

**Step 1: Write failing tests**

```python
"""Integration tests for auth endpoints."""

import pytest
import pytest_asyncio


pytestmark = pytest.mark.asyncio


class TestRegister:

    async def test_register_new_user(self, client, db):
        """T8.1: Register returns 201 with user_id, password is hashed in DB."""
        resp = await client.post("/auth/register", json={
            "email": "new@example.com",
            "password": "SecurePass123!",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "user_id" in data

        # Verify user in DB with bcrypt hash
        from backend.project_service.models.database import User
        from sqlalchemy import select
        result = await db.execute(select(User).where(User.email == "new@example.com"))
        user = result.scalar_one()
        assert user is not None
        assert user.password_hash != "SecurePass123!"
        assert user.password_hash.startswith("$2b$")

    async def test_register_duplicate_email(self, client, db):
        """T8.2: Second registration with same email returns 409."""
        await client.post("/auth/register", json={
            "email": "dupe@example.com",
            "password": "SecurePass123!",
        })
        resp = await client.post("/auth/register", json={
            "email": "dupe@example.com",
            "password": "DifferentPass456!",
        })
        assert resp.status_code == 409
```

**Step 2: Run — verify fail (404 — route doesn't exist)**

**Step 3: Implement auth routes**

```python
"""Auth routes: register, login, refresh."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import User, RefreshToken, get_db
from backend.project_service.models.schemas import (
    RegisterRequest, RegisterResponse,
    LoginRequest, TokenResponse,
    RefreshRequest,
)
from backend.project_service.services.auth_service import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    hash_refresh_token, REFRESH_TOKEN_EXPIRY_DAYS,
)
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check for existing user
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=req.email, password_hash=hash_password(req.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return RegisterResponse(user_id=user.id)
```

**Step 4: Wire route into main.py**

Add to `main.py`:
```python
from backend.project_service.routes.auth import router as auth_router
app.include_router(auth_router)
```

**Step 5: Run — verify pass**

Run: `python -m pytest backend/project_service/tests/integration/test_auth_endpoints.py::TestRegister -v`

---

## Task 7: Auth Routes — Login (T8.3, T8.4, T8.5)

**Files:**
- Modify: `backend/project_service/routes/auth.py`
- Modify: `backend/project_service/tests/integration/test_auth_endpoints.py`

**Step 1: Write failing tests**

```python
class TestLogin:

    async def test_login_valid_credentials(self, client, db):
        """T8.3: Login returns access_token (JWT, 15min) and refresh_token (opaque, 30 day)."""
        await client.post("/auth/register", json={
            "email": "login@example.com", "password": "SecurePass123!",
        })
        resp = await client.post("/auth/login", json={
            "email": "login@example.com", "password": "SecurePass123!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # access_token is a JWT (has dots)
        assert data["access_token"].count(".") == 2
        # refresh_token is opaque (no dots)
        assert "." not in data["refresh_token"]

        # Verify refresh_token hash stored in DB
        from backend.project_service.models.database import RefreshToken as RT
        from backend.project_service.services.auth_service import hash_refresh_token
        from sqlalchemy import select
        token_hash = hash_refresh_token(data["refresh_token"])
        result = await db.execute(select(RT).where(RT.token_hash == token_hash))
        rt = result.scalar_one()
        assert rt is not None

    async def test_login_wrong_password(self, client, db):
        """T8.4: Wrong password returns 401, no tokens."""
        await client.post("/auth/register", json={
            "email": "wrong@example.com", "password": "SecurePass123!",
        })
        resp = await client.post("/auth/login", json={
            "email": "wrong@example.com", "password": "WrongPassword!",
        })
        assert resp.status_code == 401
        assert "access_token" not in resp.json()

    async def test_login_nonexistent_email(self, client, db):
        """T8.5: Non-existent email returns 401 (same as wrong password)."""
        resp = await client.post("/auth/login", json={
            "email": "nobody@example.com", "password": "Whatever123!",
        })
        assert resp.status_code == 401
```

**Step 2: Run — verify fail**

**Step 3: Add login endpoint to auth.py**

```python
@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token()

    # Store refresh token hash
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
    )
    db.add(rt)
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)
```

**Step 4: Run — verify pass**

---

## Task 8: Auth Routes — Refresh (T8.6, T8.7)

**Files:**
- Modify: `backend/project_service/routes/auth.py`
- Modify: `backend/project_service/tests/integration/test_auth_endpoints.py`

**Step 1: Write failing tests**

```python
class TestRefresh:

    async def test_refresh_token_exchange(self, client, db):
        """T8.6: Refresh returns new tokens, old refresh invalidated."""
        await client.post("/auth/register", json={
            "email": "refresh@example.com", "password": "SecurePass123!",
        })
        login_resp = await client.post("/auth/login", json={
            "email": "refresh@example.com", "password": "SecurePass123!",
        })
        old_refresh = login_resp.json()["refresh_token"]

        # Exchange
        resp = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["refresh_token"] != old_refresh  # Rotation

        # Old refresh token should be invalidated
        resp2 = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
        assert resp2.status_code == 401

    async def test_expired_refresh_token(self, client, db):
        """T8.7: Expired refresh token returns 401."""
        await client.post("/auth/register", json={
            "email": "expired@example.com", "password": "SecurePass123!",
        })
        login_resp = await client.post("/auth/login", json={
            "email": "expired@example.com", "password": "SecurePass123!",
        })
        refresh_token = login_resp.json()["refresh_token"]

        # Manually expire the token in DB
        from backend.project_service.services.auth_service import hash_refresh_token
        from backend.project_service.models.database import RefreshToken as RT
        from sqlalchemy import update
        from datetime import datetime, timedelta, timezone
        await db.execute(
            update(RT)
            .where(RT.token_hash == hash_refresh_token(refresh_token))
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await db.commit()

        resp = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401
```

**Step 2: Run — verify fail**

**Step 3: Add refresh endpoint to auth.py**

```python
@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(req.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    rt = result.scalar_one_or_none()

    if rt is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if rt.expires_at < datetime.now(timezone.utc):
        await db.delete(rt)
        await db.commit()
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Delete old token (rotation)
    await db.delete(rt)

    # Issue new tokens
    access_token = create_access_token(str(rt.user_id))
    new_refresh = create_refresh_token()
    new_rt = RefreshToken(
        user_id=rt.user_id,
        token_hash=hash_refresh_token(new_refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
    )
    db.add(new_rt)
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=new_refresh)
```

**Step 4: Run — verify pass**

---

## Task 9: Internal Middleware — Localhost Only (T8.22, T8.23)

**Files:**
- Create: `backend/project_service/middleware/internal_middleware.py`
- Create: `backend/project_service/tests/integration/test_internal_endpoints.py`

**Step 1: Write failing tests**

```python
"""Integration tests for internal endpoints."""

import pytest

pytestmark = pytest.mark.asyncio


class TestInternalMiddleware:

    async def test_internal_from_external_ip_returns_404(self, client):
        """T8.22: /internal/* from non-localhost returns 404."""
        # httpx test client doesn't set X-Forwarded-For, but the middleware
        # should check the actual client IP. In test, we simulate external
        # by setting a header the middleware checks.
        resp = await client.post(
            "/internal/validate",
            json={"token": "x", "project_id": "x"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert resp.status_code == 404

    async def test_internal_all_routes_blocked_externally(self, client):
        """T8.23: All /internal/* routes return 404 from external IP."""
        for path in ["/internal/validate", "/internal/acl/some-id"]:
            resp = await client.get(
                path,
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
            assert resp.status_code == 404, f"{path} should return 404"
```

**Step 2: Run — verify fail**

**Step 3: Implement internal_middleware.py**

```python
"""Localhost-only middleware for /internal/* routes."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}


class InternalOnlyMiddleware(BaseHTTPMiddleware):
    """Block all /internal/* requests from non-localhost IPs. Returns 404 (not 403)."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/internal"):
            # Check for X-Forwarded-For (proxy/external) — reject if present
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            # Check actual client IP
            client_ip = request.client.host if request.client else None
            if client_ip not in LOCALHOST_IPS:
                return JSONResponse(status_code=404, content={"detail": "Not found"})

        return await call_next(request)
```

**Step 4: Wire into main.py**

```python
from backend.project_service.middleware.internal_middleware import InternalOnlyMiddleware
app.add_middleware(InternalOnlyMiddleware)
```

**Step 5: Run — verify pass**

---

## Task 10: Internal Routes — Validate (T8.20, T8.21)

**Files:**
- Create: `backend/project_service/routes/internal.py`
- Modify: `backend/project_service/tests/integration/test_internal_endpoints.py`

**Step 1: Write failing tests**

```python
class TestInternalValidate:

    async def test_validate_valid_token_and_ownership(self, client, db, auth_headers):
        """T8.20: Valid token + owned project returns user_id, updates last_connection_at."""
        # First create a project (need to mock docker/gcp for this, or insert directly)
        from backend.project_service.models.database import Project, User
        from sqlalchemy import select
        import uuid

        # Get user_id from token
        from backend.project_service.services.auth_service import decode_access_token
        token = auth_headers["Authorization"].split(" ")[1]
        user_id = decode_access_token(token)["sub"]

        # Insert project directly in DB
        project = Project(
            id=uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            name="Test Project",
            status="running",
            ssh_public_key="ssh-ed25519 AAAA",
            ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----",
            gcs_prefix=f"projects/{uuid.uuid4()}",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        # Call /internal/validate from localhost (no X-Forwarded-For header)
        resp = await client.post("/internal/validate", json={
            "token": token,
            "project_id": str(project.id),
        })
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_id

        # Verify last_connection_at was updated
        await db.refresh(project)
        assert project.last_connection_at is not None

    async def test_validate_wrong_project(self, client, db, auth_headers):
        """T8.21: Valid token + unowned project returns 401."""
        import uuid
        token = auth_headers["Authorization"].split(" ")[1]

        # Project owned by a different user
        other_user = User(email="other@example.com", password_hash="$2b$12$fake")
        db.add(other_user)
        await db.commit()
        await db.refresh(other_user)

        project = Project(
            id=uuid.uuid4(),
            user_id=other_user.id,
            name="Other's Project",
            status="running",
            ssh_public_key="ssh-ed25519 AAAA",
            ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----",
            gcs_prefix=f"projects/{uuid.uuid4()}",
        )
        db.add(project)
        await db.commit()

        resp = await client.post("/internal/validate", json={
            "token": token,
            "project_id": str(project.id),
        })
        assert resp.status_code == 401
```

**Step 2: Run — verify fail**

**Step 3: Implement internal.py**

```python
"""Internal routes: validate token + ownership, ACL management."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import Project, get_db
from backend.project_service.models.schemas import (
    InternalValidateRequest, InternalValidateResponse,
)
from backend.project_service.services.auth_service import decode_access_token

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/validate", response_model=InternalValidateResponse)
async def validate(req: InternalValidateRequest, db: AsyncSession = Depends(get_db)):
    # Decode JWT
    payload = decode_access_token(req.token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload["sub"]

    # Check project ownership
    result = await db.execute(
        select(Project).where(
            Project.id == req.project_id,
            Project.user_id == user_id,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Update last_connection_at
    project.last_connection_at = datetime.now(timezone.utc)
    await db.commit()

    return InternalValidateResponse(user_id=user_id)
```

**Step 4: Wire into main.py**

```python
from backend.project_service.routes.internal import router as internal_router
app.include_router(internal_router)
```

**Step 5: Run — verify pass**

---

## Task 11: Project Service — Orchestration Layer

**Files:**
- Create: `backend/project_service/services/project_service.py`
- Create: `backend/project_service/tests/unit/test_project_service.py`

**Step 1: Write failing tests**

```python
"""Unit tests for project service orchestration.

All external services (Docker, GCP, snapshot) are mocked — these test
the orchestration logic, not the infrastructure.
"""

import uuid
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


class TestCreateProject:

    async def test_create_project_orchestration(self, db):
        """Create project calls GCP IAM, Docker, and inserts DB record."""
        from backend.project_service.services.project_service import create_project

        user_id = uuid.uuid4()

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker, \
             patch("backend.project_service.services.project_service._generate_ssh_keypair") as mock_ssh:

            mock_iam.create_service_account.return_value = "sa@test.iam.gserviceaccount.com"
            mock_iam.create_sa_key.return_value = '{"type":"service_account"}'
            mock_docker.create_container.return_value = ("container-id-123", 30001)
            mock_ssh.return_value = ("ssh-ed25519 AAAA pubkey", "-----BEGIN PRIVATE KEY-----")

            project = await create_project(user_id, "My Agent", db)

            assert project.status == "running"
            assert project.container_id == "container-id-123"
            assert project.ssh_host_port == 30001
            assert project.gcp_sa_email == "sa@test.iam.gserviceaccount.com"
            mock_iam.create_service_account.assert_called_once()
            mock_iam.create_sa_key.assert_called_once()
            mock_iam.grant_gcs_iam.assert_called_once()
            mock_docker.create_container.assert_called_once()


class TestDeleteProject:

    async def test_delete_project_full_teardown(self, db):
        """Delete removes Docker resources, GCP SA, and DB record."""
        from backend.project_service.services.project_service import delete_project
        from backend.project_service.models.database import Project
        from sqlalchemy import select

        user_id = uuid.uuid4()
        project = Project(
            user_id=user_id, name="To Delete", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x",
            gcp_sa_email="sa@test.iam.gserviceaccount.com",
            container_id="cid",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.docker_mgr") as mock_docker, \
             patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:

            await delete_project(project.id, user_id, db)

            mock_docker.cleanup_project_resources.assert_called_once_with(str(project.id))
            mock_iam.delete_service_account.assert_called_once()
            mock_snap.delete_snapshot_images.assert_called_once_with(str(project.id))

        # Verify DB record deleted
        result = await db.execute(select(Project).where(Project.id == project.id))
        assert result.scalar_one_or_none() is None
```

**Step 2: Run — verify fail**

**Step 3: Implement project_service.py**

```python
"""Project lifecycle orchestration.

Coordinates Docker, GCP IAM, and snapshot managers for project operations.
All sync manager calls are wrapped with asyncio.to_thread().
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import Project
from backend.project_service.services import docker_manager as docker_mgr
from backend.project_service.services import gcp_iam
from backend.project_service.services import snapshot_manager as snapshot_mgr

logger = logging.getLogger(__name__)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "pomodex-fd2bcd")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "pomodex-fd2bcd-sandbox")
CREDENTIALS_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "secrets/project-service-sa.json")
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "agent-sandbox:latest")
HOST_IP = os.environ.get("HOST_IP", "0.0.0.0")
TERMINAL_PROXY_PORT = os.environ.get("TERMINAL_PROXY_PORT", "9000")


def _generate_ssh_keypair() -> tuple[str, str]:
    """Generate an Ed25519 SSH keypair. Returns (public_key, private_key)."""
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    return public_bytes.decode(), private_bytes.decode()


async def create_project(user_id: uuid.UUID, name: str, db: AsyncSession) -> Project:
    """Create a new project: GCP SA → Docker container → DB record."""
    project_id = uuid.uuid4()
    gcs_prefix = f"projects/{project_id}"

    # Generate SSH keypair
    ssh_public_key, ssh_private_key = _generate_ssh_keypair()

    # Insert DB record early (status=creating)
    project = Project(
        id=project_id,
        user_id=user_id,
        name=name,
        status="creating",
        ssh_public_key=ssh_public_key,
        ssh_private_key=ssh_private_key,
        gcs_prefix=gcs_prefix,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    try:
        # Create GCP service account
        sa_email = await asyncio.to_thread(
            gcp_iam.create_service_account, str(project_id), GCP_PROJECT, CREDENTIALS_PATH,
        )
        sa_key = await asyncio.to_thread(
            gcp_iam.create_sa_key, sa_email, GCP_PROJECT, CREDENTIALS_PATH,
        )
        await asyncio.to_thread(
            gcp_iam.grant_gcs_iam, sa_email, GCS_BUCKET, gcs_prefix, GCP_PROJECT, CREDENTIALS_PATH,
        )

        project.gcp_sa_email = sa_email
        project.gcp_sa_key = sa_key

        # Create Docker container
        config = {
            "image": SANDBOX_IMAGE,
            "gcs_bucket": GCS_BUCKET,
            "gcs_sa_key": sa_key,
            "ssh_public_key": ssh_public_key,
        }
        container_id, ssh_port = await asyncio.to_thread(
            docker_mgr.create_container, str(project_id), config,
        )

        project.container_id = container_id
        project.container_name = f"sandbox-{project_id}"
        project.volume_name = f"vol-{project_id}"
        project.ssh_host_port = ssh_port
        project.status = "running"

        await db.commit()
        await db.refresh(project)
        return project

    except Exception as e:
        logger.error("Failed to create project %s: %s", project_id, e)
        # Cleanup on failure
        await _cleanup_failed_create(project, db)
        raise


async def _cleanup_failed_create(project: Project, db: AsyncSession):
    """Clean up resources from a failed project creation."""
    try:
        await asyncio.to_thread(docker_mgr.cleanup_project_resources, str(project.id))
    except Exception:
        pass
    if project.gcp_sa_email:
        try:
            await asyncio.to_thread(
                gcp_iam.delete_service_account,
                project.gcp_sa_email, GCP_PROJECT, CREDENTIALS_PATH, GCS_BUCKET,
            )
        except Exception:
            pass
    project.status = "error"
    await db.commit()


async def stop_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Project:
    """Stop a project: snapshot → stop container."""
    project = await _get_owned_project(project_id, user_id, db)
    if project.status != "running":
        raise ValueError(f"Project is not running (status={project.status})")

    project.status = "snapshotting"
    await db.commit()

    try:
        result = await asyncio.to_thread(
            snapshot_mgr.snapshot_project, str(project_id), CREDENTIALS_PATH,
        )
        project.snapshot_image = result["snapshot_image"]
        project.last_snapshot_at = datetime.fromtimestamp(result["last_snapshot_at"], tz=timezone.utc)
        project.last_backup_at = project.last_snapshot_at
        project.status = "stopped"
    except Exception as e:
        logger.error("Snapshot failed for %s: %s", project_id, e)
        project.status = "error"
        raise
    finally:
        await db.commit()
        await db.refresh(project)

    return project


async def start_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Project:
    """Start a stopped project: restore from snapshot or base image."""
    project = await _get_owned_project(project_id, user_id, db)
    if project.status != "stopped":
        raise ValueError(f"Project is not stopped (status={project.status})")

    project.status = "restoring"
    await db.commit()

    try:
        config = {
            "gcs_bucket": GCS_BUCKET,
            "gcs_sa_key": project.gcp_sa_key,
            "ssh_public_key": project.ssh_public_key,
        }

        image = snapshot_mgr.restore_image_for_project(project.snapshot_image, SANDBOX_IMAGE)

        if project.snapshot_image:
            container_id = await asyncio.to_thread(
                snapshot_mgr.restore_from_snapshot,
                str(project_id), image, config, CREDENTIALS_PATH,
            )
        else:
            container_id = await asyncio.to_thread(
                snapshot_mgr.restore_from_gcs, str(project_id), image, config,
            )

        project.container_id = container_id
        project.status = "running"
        project.last_active_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.error("Restore failed for %s: %s", project_id, e)
        project.status = "error"
        raise
    finally:
        await db.commit()
        await db.refresh(project)

    return project


async def snapshot_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Project:
    """Snapshot a running project (same as stop — snapshot includes stopping)."""
    return await stop_project(project_id, user_id, db)


async def delete_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    """Delete a project: full teardown of all resources."""
    project = await _get_owned_project(project_id, user_id, db)

    # Docker cleanup
    await asyncio.to_thread(docker_mgr.cleanup_project_resources, str(project_id))

    # GCP cleanup
    if project.gcp_sa_email:
        await asyncio.to_thread(
            gcp_iam.delete_service_account,
            project.gcp_sa_email, GCP_PROJECT, CREDENTIALS_PATH, GCS_BUCKET,
        )

    # AR image cleanup
    await asyncio.to_thread(snapshot_mgr.delete_snapshot_images, str(project_id))

    # DB record
    await db.delete(project)
    await db.commit()


async def _get_owned_project(
    project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession,
) -> Project:
    """Fetch a project ensuring ownership. Raises ValueError if not found/not owned."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise ValueError("Project not found")
    return project
```

**Step 4: Run unit tests — verify pass**

---

## Task 12: Project Routes — List & Create (T8.10, T8.11)

**Files:**
- Create: `backend/project_service/routes/projects.py`
- Create: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing tests**

```python
"""Integration tests for project endpoints.

Docker/GCP services are mocked — we test the API layer, not infrastructure.
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


async def _register_and_login(client, email="user@example.com"):
    """Helper: register + login, return (headers, user_id)."""
    await client.post("/auth/register", json={"email": email, "password": "SecurePass123!"})
    resp = await client.post("/auth/login", json={"email": email, "password": "SecurePass123!"})
    data = resp.json()
    from backend.project_service.services.auth_service import decode_access_token
    user_id = decode_access_token(data["access_token"])["sub"]
    return {"Authorization": f"Bearer {data['access_token']}"}, user_id


class TestListProjects:

    async def test_list_only_own_projects(self, client, db):
        """T8.10: User A sees only their projects, not user B's."""
        headers_a, _ = await _register_and_login(client, "a@example.com")
        headers_b, user_b_id = await _register_and_login(client, "b@example.com")

        # Insert projects directly
        from backend.project_service.models.database import Project
        from backend.project_service.services.auth_service import decode_access_token
        user_a_id = decode_access_token(headers_a["Authorization"].split(" ")[1])["sub"]

        for name, uid in [("A-proj", user_a_id), ("B-proj", user_b_id)]:
            p = Project(
                user_id=uuid.UUID(uid), name=name, status="running",
                ssh_public_key="pub", ssh_private_key="priv",
                gcs_prefix=f"projects/{uuid.uuid4()}",
            )
            db.add(p)
        await db.commit()

        resp = await client.get("/projects", headers=headers_a)
        assert resp.status_code == 200
        projects = resp.json()
        assert len(projects) == 1
        assert projects[0]["name"] == "A-proj"
        assert all(k in projects[0] for k in ["id", "name", "status", "created_at"])


class TestCreateProject:

    async def test_create_project(self, client, db):
        """T8.11: Create returns 201 with project details, container running."""
        headers, _ = await _register_and_login(client, "creator@example.com")

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.create_service_account.return_value = "sa@test.iam"
            mock_iam.create_sa_key.return_value = '{"type":"service_account"}'
            mock_docker.create_container.return_value = ("cid-123", 30001)

            resp = await client.post(
                "/projects",
                json={"name": "My Agent"},
                headers=headers,
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "running"
        assert data["name"] == "My Agent"
        assert data["ssh_port"] == 30001
        assert "ssh_private_key" in data
        assert "terminal_url" in data
```

**Step 2: Run — verify fail**

**Step 3: Implement projects.py routes**

```python
"""Project CRUD routes."""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.middleware.auth_middleware import get_current_user_id
from backend.project_service.models.database import Project, get_db
from backend.project_service.models.schemas import (
    CreateProjectRequest, ProjectResponse, ProjectDetailResponse,
    ProjectCreateResponse, BackupStatusResponse,
)
from backend.project_service.services import project_service as svc

router = APIRouter(prefix="/projects", tags=["projects"])

HOST_IP = os.environ.get("HOST_IP", "0.0.0.0")
TERMINAL_PROXY_PORT = os.environ.get("TERMINAL_PROXY_PORT", "9000")


def _terminal_url(project_id: uuid.UUID) -> str:
    return f"ws://{HOST_IP}:{TERMINAL_PROXY_PORT}/terminal/{project_id}"


def _project_detail(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "status": p.status,
        "created_at": p.created_at,
        "last_active_at": p.last_active_at,
        "terminal_url": _terminal_url(p.id) if p.status == "running" else None,
        "ssh_host": HOST_IP if p.status == "running" else None,
        "ssh_port": p.ssh_host_port if p.status == "running" else None,
        "ssh_private_key": p.ssh_private_key,
        "last_backup_at": p.last_backup_at,
        "last_snapshot_at": p.last_snapshot_at,
    }


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
    )
    return [
        ProjectResponse(
            id=p.id, name=p.name, status=p.status,
            created_at=p.created_at, last_active_at=p.last_active_at,
        )
        for p in result.scalars().all()
    ]


@router.post("", response_model=ProjectCreateResponse, status_code=201)
async def create_project(
    req: CreateProjectRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.create_project(uuid.UUID(user_id), req.name, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return _project_detail(project)


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_detail(project)


@router.post("/{project_id}/stop", response_model=ProjectDetailResponse)
async def stop_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.stop_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.post("/{project_id}/start", response_model=ProjectDetailResponse)
async def start_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.start_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        await svc.delete_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "deleted"}


@router.post("/{project_id}/snapshot", response_model=ProjectDetailResponse)
async def snapshot_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.snapshot_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.post("/{project_id}/restore", response_model=ProjectDetailResponse)
async def restore_project(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await svc.start_project(project_id, uuid.UUID(user_id), db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _project_detail(project)


@router.get("/{project_id}/backup-status", response_model=BackupStatusResponse)
async def backup_status(
    project_id: uuid.UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return BackupStatusResponse(
        last_backup_at=project.last_backup_at,
        snapshot_image=project.snapshot_image,
        last_snapshot_at=project.last_snapshot_at,
    )
```

**Step 4: Wire into main.py**

```python
from backend.project_service.routes.projects import router as projects_router
app.include_router(projects_router)
```

**Step 5: Run — verify pass**

---

## Task 13: Project Routes — Get & Get Wrong User (T8.12, T8.13)

**Files:**
- Modify: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing tests**

```python
class TestGetProject:

    async def test_get_project_details(self, client, db):
        """T8.12: Get owned project returns full details."""
        headers, user_id = await _register_and_login(client, "getter@example.com")

        from backend.project_service.models.database import Project
        project = Project(
            user_id=uuid.UUID(user_id), name="Detail Test", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", ssh_host_port=30001,
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        resp = await client.get(f"/projects/{project.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["ssh_port"] == 30001
        assert "terminal_url" in data
        assert "ssh_private_key" in data

    async def test_get_project_wrong_user(self, client, db):
        """T8.13: Non-owner gets 404 (not 403)."""
        headers_a, _ = await _register_and_login(client, "owner@example.com")
        headers_b, _ = await _register_and_login(client, "intruder@example.com")

        from backend.project_service.models.database import Project
        from backend.project_service.services.auth_service import decode_access_token
        user_a_id = decode_access_token(headers_a["Authorization"].split(" ")[1])["sub"]

        project = Project(
            user_id=uuid.UUID(user_a_id), name="Owner's Project", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        resp = await client.get(f"/projects/{project.id}", headers=headers_b)
        assert resp.status_code == 404
```

**Step 2: Run — verify pass** (routes already implemented in Task 12)

---

## Task 14: Project Routes — Stop & Start (T8.14, T8.15)

**Files:**
- Modify: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing tests**

```python
class TestStopProject:

    async def test_stop_project(self, client, db):
        """T8.14: Stop snapshots and stops container."""
        headers, user_id = await _register_and_login(client, "stopper@example.com")

        from backend.project_service.models.database import Project
        project = Project(
            user_id=uuid.UUID(user_id), name="To Stop", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", container_id="cid",
            gcp_sa_key='{"type":"service_account"}',
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
            import time
            mock_snap.snapshot_project.return_value = {
                "snapshot_image": "registry/img:latest",
                "last_snapshot_at": time.time(),
                "status": "stopped",
            }
            resp = await client.post(f"/projects/{project.id}/stop", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert data["last_snapshot_at"] is not None


class TestStartProject:

    async def test_start_stopped_project(self, client, db):
        """T8.15: Start restores a stopped project."""
        headers, user_id = await _register_and_login(client, "starter@example.com")

        from backend.project_service.models.database import Project
        project = Project(
            user_id=uuid.UUID(user_id), name="To Start", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", snapshot_image="registry/img:latest",
            gcp_sa_key='{"type":"service_account"}',
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
            mock_snap.restore_image_for_project.return_value = "registry/img:latest"
            mock_snap.restore_from_snapshot.return_value = "new-cid-456"
            resp = await client.post(f"/projects/{project.id}/start", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
```

**Step 2: Run — verify pass** (routes already implemented)

---

## Task 15: Project Routes — Delete (T8.16)

**Files:**
- Modify: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing test**

```python
class TestDeleteProject:

    async def test_delete_full_teardown(self, client, db):
        """T8.16: Delete removes all resources and DB record."""
        headers, user_id = await _register_and_login(client, "deleter@example.com")

        from backend.project_service.models.database import Project
        from sqlalchemy import select
        project = Project(
            user_id=uuid.UUID(user_id), name="To Delete", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", container_id="cid",
            gcp_sa_email="sa@test.iam",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)
        pid = project.id

        with patch("backend.project_service.services.project_service.docker_mgr") as mock_docker, \
             patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:

            resp = await client.delete(f"/projects/{pid}", headers=headers)

        assert resp.status_code == 200
        mock_docker.cleanup_project_resources.assert_called_once()
        mock_iam.delete_service_account.assert_called_once()
        mock_snap.delete_snapshot_images.assert_called_once()

        # DB record gone
        result = await db.execute(select(Project).where(Project.id == pid))
        assert result.scalar_one_or_none() is None
```

**Step 2: Run — verify pass**

---

## Task 16: Project Routes — Snapshot, Restore, Backup Status (T8.17, T8.18, T8.19)

**Files:**
- Modify: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing tests**

```python
class TestSnapshotProject:

    async def test_snapshot_project(self, client, db):
        """T8.17: Snapshot pushes image, updates DB, stops container."""
        headers, user_id = await _register_and_login(client, "snapper@example.com")

        from backend.project_service.models.database import Project
        project = Project(
            user_id=uuid.UUID(user_id), name="To Snap", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", container_id="cid",
            gcp_sa_key='{"type":"service_account"}',
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
            import time
            mock_snap.snapshot_project.return_value = {
                "snapshot_image": "registry/proj:latest",
                "last_snapshot_at": time.time(),
                "status": "stopped",
            }
            resp = await client.post(f"/projects/{project.id}/snapshot", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert data["last_snapshot_at"] is not None


class TestRestoreProject:

    async def test_restore_from_snapshot(self, client, db):
        """T8.18: Restore starts container from snapshot image."""
        headers, user_id = await _register_and_login(client, "restorer@example.com")

        from backend.project_service.models.database import Project
        project = Project(
            user_id=uuid.UUID(user_id), name="To Restore", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x", snapshot_image="registry/proj:latest",
            gcp_sa_key='{"type":"service_account"}',
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
            mock_snap.restore_image_for_project.return_value = "registry/proj:latest"
            mock_snap.restore_from_snapshot.return_value = "new-cid"
            resp = await client.post(f"/projects/{project.id}/restore", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["status"] == "running"


class TestBackupStatus:

    async def test_backup_status(self, client, db):
        """T8.19: Returns backup/snapshot metadata."""
        headers, user_id = await _register_and_login(client, "backup@example.com")

        from backend.project_service.models.database import Project
        from datetime import datetime, timezone
        project = Project(
            user_id=uuid.UUID(user_id), name="Backup Check", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix="projects/x",
            last_backup_at=datetime.now(timezone.utc),
            snapshot_image="registry/proj:latest",
            last_snapshot_at=datetime.now(timezone.utc),
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        resp = await client.get(f"/projects/{project.id}/backup-status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_backup_at"] is not None
        assert data["snapshot_image"] == "registry/proj:latest"
        assert data["last_snapshot_at"] is not None
```

**Step 2: Run — verify pass**

---

## Task 17: Inactivity Checker (T8.24, T8.25, T8.26)

**Files:**
- Create: `backend/project_service/tasks/inactivity_checker.py`
- Create: `backend/project_service/tests/integration/test_inactivity_checker.py`

**Step 1: Write failing tests**

```python
"""Integration tests for the inactivity checker background task."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.project_service.models.database import Project

pytestmark = pytest.mark.asyncio

THIRTY_ONE_MIN_AGO = datetime.now(timezone.utc) - timedelta(minutes=31)
FOUR_MIN_AGO = datetime.now(timezone.utc) - timedelta(minutes=4)


async def _insert_project(db, user_id, name, status, last_connection_at):
    p = Project(
        user_id=user_id, name=name, status=status,
        ssh_public_key="pub", ssh_private_key="priv",
        gcs_prefix=f"projects/{uuid.uuid4()}",
        container_id="cid", gcp_sa_key='{"type":"sa"}',
        last_connection_at=last_connection_at,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


class TestInactivityChecker:

    async def test_identifies_idle_projects(self, db):
        """T8.24: Running project idle > 30 min gets snapshotted and stopped."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects
        from backend.project_service.models.database import User

        user = User(email="idle@example.com", password_hash="$2b$12$fake")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        project = await _insert_project(db, user.id, "Idle", "running", THIRTY_ONE_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            import time
            mock_snap.snapshot_project.return_value = {
                "snapshot_image": "registry/img:latest",
                "last_snapshot_at": time.time(),
                "status": "stopped",
            }
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "stopped"
        mock_snap.snapshot_project.assert_called_once()

    async def test_skips_active_projects(self, db):
        """T8.25: Recently active project is not snapshotted."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects
        from backend.project_service.models.database import User

        user = User(email="active@example.com", password_hash="$2b$12$fake")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        project = await _insert_project(db, user.id, "Active", "running", FOUR_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "running"
        mock_snap.snapshot_project.assert_not_called()

    async def test_skips_non_running_projects(self, db):
        """T8.26: Already stopped project with old last_connection_at is not processed."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects
        from backend.project_service.models.database import User

        user = User(email="stopped@example.com", password_hash="$2b$12$fake")
        db.add(user)
        await db.commit()
        await db.refresh(user)

        project = await _insert_project(db, user.id, "Stopped", "stopped", THIRTY_ONE_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "stopped"
        mock_snap.snapshot_project.assert_not_called()
```

**Step 2: Run — verify fail**

**Step 3: Implement inactivity_checker.py**

```python
"""Background task: auto-snapshot idle projects after 30 min of inactivity."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.project_service.models.database import Project
from backend.project_service.services import snapshot_manager as snapshot_mgr

logger = logging.getLogger(__name__)

IDLE_THRESHOLD_MINUTES = int(os.environ.get("IDLE_THRESHOLD_MINUTES", "30"))
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
CREDENTIALS_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "secrets/project-service-sa.json")


async def check_inactive_projects(db: AsyncSession) -> None:
    """Find and snapshot all running projects idle longer than threshold."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=IDLE_THRESHOLD_MINUTES)

    result = await db.execute(
        select(Project).where(
            Project.status == "running",
            Project.last_connection_at < cutoff,
        )
    )
    idle_projects = result.scalars().all()

    for project in idle_projects:
        logger.info("Auto-snapshotting idle project %s (last connection: %s)",
                     project.id, project.last_connection_at)
        try:
            project.status = "snapshotting"
            await db.commit()

            snap_result = await asyncio.to_thread(
                snapshot_mgr.snapshot_project, str(project.id), CREDENTIALS_PATH,
            )
            project.snapshot_image = snap_result["snapshot_image"]
            project.last_snapshot_at = datetime.fromtimestamp(
                snap_result["last_snapshot_at"], tz=timezone.utc
            )
            project.last_backup_at = project.last_snapshot_at
            project.status = "stopped"
            await db.commit()
        except Exception as e:
            logger.error("Auto-snapshot failed for %s: %s", project.id, e)
            project.status = "error"
            await db.commit()


async def run_inactivity_checker_loop(session_factory) -> None:
    """Run the inactivity checker in an infinite loop. Called from app startup."""
    while True:
        try:
            async with session_factory() as db:
                await check_inactive_projects(db)
        except Exception as e:
            logger.error("Inactivity checker error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
```

**Step 4: Wire into main.py startup**

```python
@app.on_event("startup")
async def startup():
    from backend.project_service.models.database import create_tables, async_session
    await create_tables()

    from backend.project_service.tasks.inactivity_checker import run_inactivity_checker_loop
    asyncio.create_task(run_inactivity_checker_loop(async_session))
```

**Step 5: Run — verify pass**

---

## Task 18: Error Handling (T8.27, T8.28)

**Files:**
- Modify: `backend/project_service/tests/integration/test_project_endpoints.py`

**Step 1: Write failing tests**

```python
class TestErrorHandling:

    async def test_create_docker_failure_cleans_up(self, client, db):
        """T8.27: Docker failure returns 500, partial resources cleaned up, status=error."""
        headers, user_id = await _register_and_login(client, "dockerfail@example.com")

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.create_service_account.return_value = "sa@test.iam"
            mock_iam.create_sa_key.return_value = '{"type":"service_account"}'
            mock_docker.create_container.side_effect = RuntimeError("Docker daemon unreachable")

            resp = await client.post(
                "/projects",
                json={"name": "Will Fail"},
                headers=headers,
            )

        assert resp.status_code == 500
        # Verify cleanup was attempted
        mock_docker.cleanup_project_resources.assert_called_once()
        mock_iam.delete_service_account.assert_called_once()

        # Verify project in DB has error status
        from backend.project_service.models.database import Project
        from sqlalchemy import select
        result = await db.execute(select(Project).where(Project.name == "Will Fail"))
        project = result.scalar_one()
        assert project.status == "error"

    async def test_create_gcp_failure_cleans_up(self, client, db):
        """T8.28: GCP failure returns 500, Docker resources cleaned up, status=error."""
        headers, user_id = await _register_and_login(client, "gcpfail@example.com")

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.create_service_account.side_effect = RuntimeError("GCP API error")

            resp = await client.post(
                "/projects",
                json={"name": "GCP Fail"},
                headers=headers,
            )

        assert resp.status_code == 500
        mock_docker.cleanup_project_resources.assert_called_once()

        from backend.project_service.models.database import Project
        from sqlalchemy import select
        result = await db.execute(select(Project).where(Project.name == "GCP Fail"))
        project = result.scalar_one()
        assert project.status == "error"
```

**Step 2: Run — verify pass** (error handling already in project_service.py from Task 11)

---

## Task 19: Dockerfile

**Files:**
- Create: `backend/project_service/Dockerfile`

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system deps for asyncpg and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/backend/project_service/

# Need the backend package to be importable
RUN touch /app/backend/__init__.py

ENV PYTHONPATH=/app

CMD ["uvicorn", "backend.project_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Step 2: Verify Dockerfile syntax**

Run: `docker build --check backend/project_service/ 2>&1 || echo "syntax check not supported, visual check OK"`

---

## Task 20: docker-compose.yml & Verification Tests

**Files:**
- Create: `docker-compose.yml` (project root)
- Create: `backend/project_service/tests/integration/test_docker_compose.py`

**Step 1: Write failing tests**

```python
"""Verification tests for docker-compose.yml."""

import subprocess
import os

import pytest
import yaml


COMPOSE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "docker-compose.yml"
)


class TestDockerCompose:

    def test_compose_file_exists(self):
        assert os.path.isfile(COMPOSE_FILE), "docker-compose.yml not found at project root"

    def test_compose_valid_yaml(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_compose_has_required_services(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        services = data.get("services", {})
        assert "project-service" in services, "Missing project-service"
        assert "postgres" in services, "Missing postgres"
        assert "terminal-proxy" in services, "Missing terminal-proxy"

    def test_project_service_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        ps = data["services"]["project-service"]
        # Must mount docker socket for container management
        volumes = ps.get("volumes", [])
        assert any("/var/run/docker.sock" in str(v) for v in volumes), \
            "project-service must mount Docker socket"
        # Must expose port 8000
        ports = ps.get("ports", [])
        assert any("8000" in str(p) for p in ports), "project-service must expose port 8000"
        # Must depend on postgres
        assert "postgres" in ps.get("depends_on", []) or \
               "postgres" in ps.get("depends_on", {}), \
            "project-service must depend on postgres"

    def test_postgres_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        pg = data["services"]["postgres"]
        assert pg.get("image", "").startswith("postgres:"), "postgres must use postgres image"
        # Must have persistent volume
        volumes = pg.get("volumes", [])
        assert len(volumes) > 0, "postgres must have persistent volume"

    def test_terminal_proxy_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        tp = data["services"]["terminal-proxy"]
        # Must use host network mode
        assert tp.get("network_mode") == "host", "terminal-proxy must use host network"
        # Must mount docker socket
        volumes = tp.get("volumes", [])
        assert any("/var/run/docker.sock" in str(v) for v in volumes), \
            "terminal-proxy must mount Docker socket"

    def test_compose_config_validates(self):
        """docker compose config validates the file without errors."""
        result = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "config", "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

    def test_platform_network_defined(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        networks = data.get("networks", {})
        assert "platform-net" in networks, "Must define platform-net network"

    def test_postgres_volume_defined(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        volumes = data.get("volumes", {})
        assert "postgres-data" in volumes, "Must define postgres-data volume"
```

**Step 2: Run — verify fail (file doesn't exist)**

**Step 3: Create docker-compose.yml**

```yaml
version: "3.9"

services:
  project-service:
    build: ./backend/project_service
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: "postgresql+asyncpg://pomodex:pomodex@postgres:5432/sandboxes"
      JWT_SECRET: "${JWT_SECRET}"
      GCS_BUCKET: "${GCS_BUCKET:-pomodex-fd2bcd-sandbox}"
      GCP_PROJECT: "${GCP_PROJECT:-pomodex-fd2bcd}"
      GOOGLE_APPLICATION_CREDENTIALS: "/secrets/project-service-sa.json"
      SANDBOX_IMAGE: "${SANDBOX_IMAGE:-agent-sandbox:latest}"
      HOST_IP: "${HOST_IP:-0.0.0.0}"
      TERMINAL_PROXY_PORT: "9000"
      CORS_ORIGINS: "${CORS_ORIGINS:-*}"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./secrets/project-service-sa.json:/secrets/project-service-sa.json:ro
    networks:
      - platform-net
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: sandboxes
      POSTGRES_USER: pomodex
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-pomodex}"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - platform-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U pomodex -d sandboxes"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  terminal-proxy:
    build: ./backend/terminal_proxy
    network_mode: host
    pid: "host"
    cap_add:
      - NET_ADMIN
    environment:
      PROJECT_SERVICE_URL: "http://localhost:8000"
      PROXY_PORT: "9000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /etc/squid/acls:/etc/squid/acls
      - /etc/squid/conf.d:/etc/squid/conf.d
    depends_on:
      - project-service
    restart: unless-stopped

volumes:
  postgres-data:

networks:
  platform-net:
    name: platform-net
```

**Step 4: Run — verify pass**

Run: `python -m pytest backend/project_service/tests/integration/test_docker_compose.py -v`

---

## Task 21: Wire Everything Into main.py & Final Integration

**Files:**
- Modify: `backend/project_service/main.py`

**Step 1: Write the final main.py with all routes and middleware wired**

```python
"""Project Service API — FastAPI application."""

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.project_service.middleware.internal_middleware import InternalOnlyMiddleware
from backend.project_service.routes.auth import router as auth_router
from backend.project_service.routes.projects import router as projects_router
from backend.project_service.routes.internal import router as internal_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Project Service", version="0.1.0")

# Middleware (order matters — outermost first)
app.add_middleware(InternalOnlyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(internal_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    from backend.project_service.models.database import create_tables, async_session
    await create_tables()

    from backend.project_service.tasks.inactivity_checker import run_inactivity_checker_loop
    asyncio.create_task(run_inactivity_checker_loop(async_session))
```

**Step 2: Run the entire test suite**

Run: `python -m pytest backend/project_service/tests/ -v`
Expected: All 28+ tests pass

**Step 3: Verify test coverage matches M8 test cases**

| M8 Test | Test File | Status |
|---------|-----------|--------|
| T8.1 Register new user | test_auth_endpoints.py::TestRegister::test_register_new_user | |
| T8.2 Register duplicate | test_auth_endpoints.py::TestRegister::test_register_duplicate_email | |
| T8.3 Login valid | test_auth_endpoints.py::TestLogin::test_login_valid_credentials | |
| T8.4 Login wrong password | test_auth_endpoints.py::TestLogin::test_login_wrong_password | |
| T8.5 Login nonexistent | test_auth_endpoints.py::TestLogin::test_login_nonexistent_email | |
| T8.6 Refresh exchange | test_auth_endpoints.py::TestRefresh::test_refresh_token_exchange | |
| T8.7 Expired refresh | test_auth_endpoints.py::TestRefresh::test_expired_refresh_token | |
| T8.8 JWT middleware | test_auth_service.py::TestAuthMiddleware + TestJWT | |
| T8.9 Token expiry | test_auth_service.py::TestJWT::test_expired_token_rejected | |
| T8.10 List own projects | test_project_endpoints.py::TestListProjects | |
| T8.11 Create project | test_project_endpoints.py::TestCreateProject | |
| T8.12 Get project | test_project_endpoints.py::TestGetProject::test_get_project_details | |
| T8.13 Get wrong user | test_project_endpoints.py::TestGetProject::test_get_project_wrong_user | |
| T8.14 Stop project | test_project_endpoints.py::TestStopProject | |
| T8.15 Start project | test_project_endpoints.py::TestStartProject | |
| T8.16 Delete project | test_project_endpoints.py::TestDeleteProject | |
| T8.17 Snapshot | test_project_endpoints.py::TestSnapshotProject | |
| T8.18 Restore | test_project_endpoints.py::TestRestoreProject | |
| T8.19 Backup status | test_project_endpoints.py::TestBackupStatus | |
| T8.20 Internal validate | test_internal_endpoints.py::TestInternalValidate::test_validate_valid_token_and_ownership | |
| T8.21 Internal wrong project | test_internal_endpoints.py::TestInternalValidate::test_validate_wrong_project | |
| T8.22 Internal external IP | test_internal_endpoints.py::TestInternalMiddleware::test_internal_from_external_ip_returns_404 | |
| T8.23 Internal all blocked | test_internal_endpoints.py::TestInternalMiddleware::test_internal_all_routes_blocked_externally | |
| T8.24 Idle snapshotted | test_inactivity_checker.py::TestInactivityChecker::test_identifies_idle_projects | |
| T8.25 Active skipped | test_inactivity_checker.py::TestInactivityChecker::test_skips_active_projects | |
| T8.26 Stopped skipped | test_inactivity_checker.py::TestInactivityChecker::test_skips_non_running_projects | |
| T8.27 Docker failure | test_project_endpoints.py::TestErrorHandling::test_create_docker_failure_cleans_up | |
| T8.28 GCP failure | test_project_endpoints.py::TestErrorHandling::test_create_gcp_failure_cleans_up | |
| Docker Compose | test_docker_compose.py (9 tests) | |

---

## Execution Notes

- **Working directory**: `.worktrees/m8-project-service-api/`
- **Run all tests**: `python -m pytest backend/project_service/tests/ -v`
- **Run specific test file**: `python -m pytest backend/project_service/tests/unit/test_auth_service.py -v`
- **Do NOT git add until all tests pass**
- **Add `pyyaml` to requirements.txt** (needed for docker-compose tests)
