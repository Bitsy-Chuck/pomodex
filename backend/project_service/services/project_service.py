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
    """Create a new project: GCP SA -> Docker container -> DB record."""
    project_id = uuid.uuid4()
    gcs_prefix = f"projects/{project_id}"

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
    """Stop a project: snapshot -> stop container."""
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
    """Snapshot a running project (delegates to stop which snapshots first)."""
    return await stop_project(project_id, user_id, db)


async def delete_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> None:
    """Delete a project: full teardown of all resources."""
    project = await _get_owned_project(project_id, user_id, db)

    await asyncio.to_thread(docker_mgr.cleanup_project_resources, str(project_id))

    if project.gcp_sa_email:
        await asyncio.to_thread(
            gcp_iam.delete_service_account,
            project.gcp_sa_email, GCP_PROJECT, CREDENTIALS_PATH, GCS_BUCKET,
        )

    await asyncio.to_thread(snapshot_mgr.delete_snapshot_images, str(project_id))

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
