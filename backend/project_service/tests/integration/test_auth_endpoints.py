"""Integration tests for auth endpoints (T8.1-T8.7)."""

import pytest

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
