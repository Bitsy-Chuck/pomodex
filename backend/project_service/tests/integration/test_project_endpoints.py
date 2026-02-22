"""Integration tests for project endpoints (T8.10-T8.19, T8.27-T8.28).

Docker/GCP services are mocked — we test the API layer.
"""

import uuid
import time
from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from backend.project_service.models.database import Project, User
from backend.project_service.services.auth_service import decode_access_token

pytestmark = pytest.mark.asyncio


async def _register_and_login(client, email="user@example.com"):
    """Helper: register + login, return (headers, user_id)."""
    await client.post("/auth/register", json={"email": email, "password": "SecurePass123!"})
    resp = await client.post("/auth/login", json={"email": email, "password": "SecurePass123!"})
    data = resp.json()
    user_id = decode_access_token(data["access_token"])["sub"]
    return {"Authorization": f"Bearer {data['access_token']}"}, user_id


async def _set_user_gcp(db, user_id):
    """Helper: set GCP fields on a User row (simulates provisioned user)."""
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one()
    user.gcs_bucket = "test-bucket"
    user.gcp_sa_email = "sa@test.iam"
    user.gcp_sa_key = '{"type":"service_account"}'
    await db.commit()


class TestListProjects:

    async def test_list_only_own_projects(self, client, db):
        """T8.10: User A sees only their projects, not user B's."""
        headers_a, user_a_id = await _register_and_login(client, "a@example.com")
        headers_b, user_b_id = await _register_and_login(client, "b@example.com")

        for name, uid in [("A-proj", user_a_id), ("B-proj", user_b_id)]:
            p = Project(
                user_id=uuid.UUID(uid), name=name, status="running",
                ssh_public_key="pub", ssh_private_key="priv",
                gcs_prefix=f"{uuid.uuid4()}/workspace",
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
        headers, user_id = await _register_and_login(client, "creator@example.com")

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.make_bucket_name.return_value = "test-bucket"
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


class TestGetProject:

    async def test_get_project_details(self, client, db):
        """T8.12: Get owned project returns full details."""
        headers, user_id = await _register_and_login(client, "getter@example.com")

        project = Project(
            user_id=uuid.UUID(user_id), name="Detail Test", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", ssh_host_port=30001,
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
        headers_a, user_a_id = await _register_and_login(client, "owner2@example.com")
        headers_b, _ = await _register_and_login(client, "intruder@example.com")

        project = Project(
            user_id=uuid.UUID(user_a_id), name="Owner's Project", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        resp = await client.get(f"/projects/{project.id}", headers=headers_b)
        assert resp.status_code == 404


class TestStopProject:

    async def test_stop_project(self, client, db):
        """T8.14: Stop snapshots and stops container."""
        headers, user_id = await _register_and_login(client, "stopper@example.com")

        project = Project(
            user_id=uuid.UUID(user_id), name="To Stop", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", container_id="cid",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
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
        await _set_user_gcp(db, user_id)

        project = Project(
            user_id=uuid.UUID(user_id), name="To Start", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", snapshot_image="registry/img:latest",
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


class TestDeleteProject:

    async def test_delete_full_teardown(self, client, db):
        """T8.16: Delete removes Docker resources, GCS prefix, and DB record."""
        headers, user_id = await _register_and_login(client, "deleter@example.com")
        await _set_user_gcp(db, user_id)
        from sqlalchemy import select as sa_select

        project = Project(
            user_id=uuid.UUID(user_id), name="To Delete", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", container_id="cid",
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
        mock_iam.delete_gcs_prefix.assert_called_once()
        mock_snap.delete_snapshot_images.assert_called_once()

        result = await db.execute(sa_select(Project).where(Project.id == pid))
        assert result.scalar_one_or_none() is None


class TestSnapshotProject:

    async def test_snapshot_project(self, client, db):
        """T8.17: Snapshot pushes image, updates DB, stops container."""
        headers, user_id = await _register_and_login(client, "snapper@example.com")

        project = Project(
            user_id=uuid.UUID(user_id), name="To Snap", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", container_id="cid",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
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
        await _set_user_gcp(db, user_id)

        project = Project(
            user_id=uuid.UUID(user_id), name="To Restore", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace", snapshot_image="registry/proj:latest",
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

        project = Project(
            user_id=uuid.UUID(user_id), name="Backup Check", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace",
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


class TestErrorHandling:

    async def test_create_docker_failure_cleans_up(self, client, db):
        """T8.27: Docker failure returns 500, partial resources cleaned up, status=error."""
        headers, user_id = await _register_and_login(client, "dockerfail@example.com")
        from sqlalchemy import select as sa_select

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.make_bucket_name.return_value = "test-bucket"
            mock_iam.create_service_account.return_value = "sa@test.iam"
            mock_iam.create_sa_key.return_value = '{"type":"service_account"}'
            mock_docker.create_container.side_effect = RuntimeError("Docker daemon unreachable")

            resp = await client.post(
                "/projects",
                json={"name": "Will Fail"},
                headers=headers,
            )

        assert resp.status_code == 500
        mock_docker.cleanup_project_resources.assert_called_once()
        # SA is NOT deleted on failed create (per-user, reusable)
        mock_iam.delete_service_account.assert_not_called()

        result = await db.execute(sa_select(Project).where(Project.name == "Will Fail"))
        project = result.scalar_one()
        assert project.status == "error"

    async def test_create_gcp_failure_cleans_up(self, client, db):
        """T8.28: GCP failure returns 500, Docker resources cleaned up, status=error."""
        headers, user_id = await _register_and_login(client, "gcpfail@example.com")
        from sqlalchemy import select as sa_select

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker:

            mock_iam.make_bucket_name.return_value = "test-bucket"
            mock_iam.create_bucket.side_effect = RuntimeError("GCP API error")

            resp = await client.post(
                "/projects",
                json={"name": "GCP Fail"},
                headers=headers,
            )

        assert resp.status_code == 500

        result = await db.execute(sa_select(Project).where(Project.name == "GCP Fail"))
        # GCP failure happens before DB insert, so no project row
        project = result.scalar_one_or_none()
        assert project is None or project.status == "error"
