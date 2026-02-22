"""Unit tests for project service orchestration."""

import uuid
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.project_service.models.database import Project, User

pytestmark = pytest.mark.asyncio


async def _create_user(db, user_id=None):
    """Helper: insert a User row and return its id."""
    uid = user_id or uuid.uuid4()
    user = User(id=uid, email=f"{uid}@test.com", password_hash="fakehash")
    db.add(user)
    await db.commit()
    return uid


class TestCreateProject:

    async def test_create_project_orchestration(self, db):
        """Create project calls GCP IAM, Docker, and inserts DB record."""
        from backend.project_service.services.project_service import create_project

        user_id = await _create_user(db)

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

        user_id = await _create_user(db)
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

        result = await db.execute(select(Project).where(Project.id == project.id))
        assert result.scalar_one_or_none() is None
