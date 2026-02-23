"""Unit tests for project service orchestration."""

import uuid
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.project_service.models.database import Project, User

pytestmark = pytest.mark.asyncio


async def _create_user(db, user_id=None, with_gcp=False):
    """Helper: insert a User row and return its id."""
    uid = user_id or uuid.uuid4()
    user = User(id=uid, email=f"{uid}@test.com", password_hash="fakehash")
    if with_gcp:
        user.gcs_bucket = "test-bucket"
        user.gcp_sa_email = "sa@test.iam.gserviceaccount.com"
        user.gcp_sa_key = '{"type":"service_account"}'
    db.add(user)
    await db.commit()
    return uid


class TestCreateProject:

    async def test_create_project_first_project_provisions_gcp(self, db):
        """First project for a user creates bucket + SA, then Docker container."""
        from backend.project_service.services.project_service import create_project

        user_id = await _create_user(db)

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker, \
             patch("backend.project_service.services.project_service._generate_ssh_keypair") as mock_ssh:

            mock_iam.make_bucket_name.return_value = "test-bucket"
            mock_iam.create_service_account.return_value = "sa@test.iam.gserviceaccount.com"
            mock_iam.create_sa_key.return_value = '{"type":"service_account"}'
            mock_docker.create_container.return_value = ("container-id-123", 30001)
            mock_ssh.return_value = ("ssh-ed25519 AAAA pubkey", "-----BEGIN PRIVATE KEY-----")

            project = await create_project(user_id, "My Agent", db)

            assert project.status == "running"
            assert project.container_id == "container-id-123"
            assert project.ssh_host_port == 30001
            mock_iam.create_bucket.assert_called_once()
            mock_iam.create_service_account.assert_called_once()
            mock_iam.create_sa_key.assert_called_once()
            mock_iam.grant_bucket_iam.assert_called_once()
            mock_docker.create_container.assert_called_once()

        # Verify User row has GCP fields
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        assert user.gcs_bucket == "test-bucket"
        assert user.gcp_sa_email == "sa@test.iam.gserviceaccount.com"

    async def test_create_second_project_reuses_gcp(self, db):
        """Second project for a user reuses existing bucket + SA (no GCP calls)."""
        from backend.project_service.services.project_service import create_project

        user_id = await _create_user(db, with_gcp=True)

        with patch("backend.project_service.services.project_service.gcp_iam") as mock_iam, \
             patch("backend.project_service.services.project_service.docker_mgr") as mock_docker, \
             patch("backend.project_service.services.project_service._generate_ssh_keypair") as mock_ssh:

            mock_docker.create_container.return_value = ("container-id-456", 30002)
            mock_ssh.return_value = ("ssh-ed25519 AAAA pubkey", "-----BEGIN PRIVATE KEY-----")

            project = await create_project(user_id, "Second Agent", db)

            assert project.status == "running"
            # No GCP provisioning calls for second project
            mock_iam.create_bucket.assert_not_called()
            mock_iam.create_service_account.assert_not_called()
            mock_iam.create_sa_key.assert_not_called()
            mock_iam.grant_bucket_iam.assert_not_called()


class TestListSnapshots:

    async def test_list_snapshots_returns_sorted(self, db):
        """list_snapshots returns tags sorted newest first."""
        from backend.project_service.services.project_service import list_snapshots

        user_id = await _create_user(db, with_gcp=True)
        project = Project(
            user_id=user_id, name="Snap List", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        from datetime import datetime, timezone
        mock_snapshots = [
            {"tag": "20260223-100000", "created_at": datetime(2026, 2, 23, 10, 0, 0, tzinfo=timezone.utc)},
            {"tag": "20260223-143015", "created_at": datetime(2026, 2, 23, 14, 30, 15, tzinfo=timezone.utc)},
            {"tag": "20260222-090000", "created_at": datetime(2026, 2, 22, 9, 0, 0, tzinfo=timezone.utc)},
        ]

        with patch("backend.project_service.services.project_service.snapshot_mgr") as mock_snap:
            mock_snap.list_snapshots.return_value = mock_snapshots

            result = await list_snapshots(project.id, user_id, db)

        assert len(result) == 3
        assert result[0]["tag"] == "20260223-100000"
        mock_snap.list_snapshots.assert_called_once_with(str(project.id))

    async def test_list_snapshots_wrong_owner_raises(self, db):
        """list_snapshots raises ValueError if user doesn't own project."""
        from backend.project_service.services.project_service import list_snapshots

        user_id = await _create_user(db, with_gcp=True)
        other_user_id = await _create_user(db)
        project = Project(
            user_id=user_id, name="Not Yours", status="stopped",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace",
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        with pytest.raises(ValueError, match="Project not found"):
            await list_snapshots(project.id, other_user_id, db)


class TestDeleteProject:

    async def test_delete_project_cleans_prefix_not_sa(self, db):
        """Delete removes Docker resources and GCS prefix, but NOT SA/bucket."""
        from backend.project_service.services.project_service import delete_project

        user_id = await _create_user(db, with_gcp=True)
        project = Project(
            user_id=user_id, name="To Delete", status="running",
            ssh_public_key="pub", ssh_private_key="priv",
            gcs_prefix=f"{uuid.uuid4()}/workspace",
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
            mock_iam.delete_gcs_prefix.assert_called_once()
            mock_snap.delete_snapshot_images.assert_called_once_with(str(project.id))
            # SA and bucket are NOT deleted
            mock_iam.delete_service_account.assert_not_called()
            mock_iam.delete_bucket.assert_not_called()

        result = await db.execute(select(Project).where(Project.id == project.id))
        assert result.scalar_one_or_none() is None
