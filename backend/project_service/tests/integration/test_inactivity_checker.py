"""Integration tests for the inactivity checker background task (T8.24-T8.26)."""

import uuid
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.project_service.models.database import Project, User

pytestmark = pytest.mark.asyncio

THIRTY_ONE_MIN_AGO = datetime.now(timezone.utc) - timedelta(minutes=31)
FOUR_MIN_AGO = datetime.now(timezone.utc) - timedelta(minutes=4)


async def _insert_project(db, user_id, name, status, last_connection_at):
    p = Project(
        user_id=user_id, name=name, status=status,
        ssh_public_key="pub", ssh_private_key="priv",
        gcs_prefix=f"{uuid.uuid4()}/workspace",
        container_id="cid",
        last_connection_at=last_connection_at,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _create_user(db, email):
    user = User(email=email, password_hash="$2b$12$fakehashfakehashfakehashfakehashfakehashfakehashfake")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


class TestInactivityChecker:

    async def test_identifies_idle_projects(self, db):
        """T8.24: Running project idle > 30 min gets snapshotted and stopped."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects

        user = await _create_user(db, "idle@example.com")
        project = await _insert_project(db, user.id, "Idle", "running", THIRTY_ONE_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            mock_snap.snapshot_project.return_value = {
                "snapshot_image": "registry/img:latest",
                "last_snapshot_at": time.time(),
                "status": "stopped",
            }
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "stopped"
        assert project.snapshot_image == "registry/img:latest"
        mock_snap.snapshot_project.assert_called_once()

    async def test_skips_active_projects(self, db):
        """T8.25: Recently active project is not snapshotted."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects

        user = await _create_user(db, "active@example.com")
        project = await _insert_project(db, user.id, "Active", "running", FOUR_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "running"
        mock_snap.snapshot_project.assert_not_called()

    async def test_skips_non_running_projects(self, db):
        """T8.26: Already stopped project with old last_connection_at is not processed."""
        from backend.project_service.tasks.inactivity_checker import check_inactive_projects

        user = await _create_user(db, "stopped@example.com")
        project = await _insert_project(db, user.id, "Stopped", "stopped", THIRTY_ONE_MIN_AGO)

        with patch("backend.project_service.tasks.inactivity_checker.snapshot_mgr") as mock_snap:
            await check_inactive_projects(db)

        await db.refresh(project)
        assert project.status == "stopped"
        mock_snap.snapshot_project.assert_not_called()
