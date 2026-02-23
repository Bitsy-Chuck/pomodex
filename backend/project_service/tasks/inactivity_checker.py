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

# If a project has been in a transitional state longer than this, consider it stuck
STUCK_THRESHOLD_MINUTES = int(os.environ.get("STUCK_THRESHOLD_MINUTES", "10"))


async def recover_stuck_projects(db: AsyncSession) -> None:
    """Reset projects stuck in transitional states (snapshotting/restoring/creating).

    Called on startup and periodically from the inactivity checker loop.
    Projects stuck longer than STUCK_THRESHOLD_MINUTES are reset to 'error'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_THRESHOLD_MINUTES)
    result = await db.execute(
        select(Project).where(
            Project.status.in_(["snapshotting", "restoring", "creating"]),
            Project.last_active_at < cutoff,
        )
    )
    stuck_projects = result.scalars().all()

    for project in stuck_projects:
        logger.warning(
            "Recovering stuck project %s (status=%s, last_active=%s) -> error",
            project.id, project.status, project.last_active_at,
        )
        project.status = "error"

    if stuck_projects:
        await db.commit()
        logger.info("Recovered %d stuck project(s)", len(stuck_projects))


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
                await recover_stuck_projects(db)
                await check_inactive_projects(db)
        except Exception as e:
            logger.error("Inactivity checker error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
