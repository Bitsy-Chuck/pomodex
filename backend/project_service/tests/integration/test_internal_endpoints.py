"""Integration tests for internal endpoints (T8.20-T8.23)."""

import uuid

import pytest

from backend.project_service.models.database import Project, User

pytestmark = pytest.mark.asyncio


class TestInternalMiddleware:

    async def test_internal_from_external_ip_returns_404(self, client):
        """T8.22: /internal/validate from non-localhost returns 404."""
        resp = await client.post(
            "/internal/validate",
            json={"token": "x", "project_id": "x"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert resp.status_code == 404

    async def test_internal_all_routes_blocked_externally(self, client):
        """T8.23: All /internal/* routes return 404 from external IP."""
        for path in ["/internal/validate", "/internal/acl/some-id"]:
            resp = await client.post(
                path,
                json={},
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
            assert resp.status_code == 404, f"{path} should return 404 from external"


class TestInternalValidate:

    async def test_validate_valid_token_and_ownership(self, client, db, auth_headers):
        """T8.20: Valid token + owned project returns user_id, updates last_connection_at."""
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
            gcs_prefix=f"{uuid.uuid4()}/workspace",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        assert project.last_connection_at is None

        # Call /internal/validate (no X-Forwarded-For = localhost)
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
        token = auth_headers["Authorization"].split(" ")[1]

        # Project owned by a different user
        other_user = User(email="other@example.com", password_hash="$2b$12$fakehashfakehashfakehashfakehashfakehashfakehashfake")
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
            gcs_prefix=f"{uuid.uuid4()}/workspace",
        )
        db.add(project)
        await db.commit()

        resp = await client.post("/internal/validate", json={
            "token": token,
            "project_id": str(project.id),
        })
        assert resp.status_code == 401
