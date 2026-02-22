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

from backend.project_service.models.database import Project, User
from backend.project_service.services import docker_manager as docker_mgr
from backend.project_service.services import gcp_iam
from backend.project_service.services import snapshot_manager as snapshot_mgr

logger = logging.getLogger(__name__)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "pomodex-fd2bcd")
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


async def _ensure_user_gcp_resources(user: User, db: AsyncSession) -> None:
    """Ensure the user has a GCS bucket + service account.

    Each step commits immediately so retries skip already-completed work.
    """
    if user.gcp_sa_key:
        return  # Fully provisioned

    user_id_str = str(user.id)
    logger.info("[user:%s] Provisioning GCP resources...", user_id_str)

    if not user.gcs_bucket:
        bucket_name = gcp_iam.make_bucket_name(user_id_str, GCP_PROJECT)
        logger.info("[user:%s] Creating GCS bucket %s...", user_id_str, bucket_name)
        await asyncio.to_thread(
            gcp_iam.create_bucket, bucket_name, GCP_PROJECT, CREDENTIALS_PATH,
        )
        user.gcs_bucket = bucket_name
        await db.commit()

    if not user.gcp_sa_email:
        logger.info("[user:%s] Creating GCP service account...", user_id_str)
        sa_email = await asyncio.to_thread(
            gcp_iam.create_service_account, user_id_str, GCP_PROJECT, CREDENTIALS_PATH,
        )
        user.gcp_sa_email = sa_email
        await db.commit()

        logger.info("[user:%s] Granting bucket IAM on %s...", user_id_str, user.gcs_bucket)
        await asyncio.to_thread(
            gcp_iam.grant_bucket_iam, user.gcp_sa_email, user.gcs_bucket, GCP_PROJECT, CREDENTIALS_PATH,
        )

    if not user.gcp_sa_key:
        logger.info("[user:%s] Creating SA key...", user_id_str)
        sa_key = await asyncio.to_thread(
            gcp_iam.create_sa_key, user.gcp_sa_email, GCP_PROJECT, CREDENTIALS_PATH,
        )
        user.gcp_sa_key = sa_key
        await db.commit()

    logger.info("[user:%s] GCP resources provisioned", user_id_str)


async def create_project(user_id: uuid.UUID, name: str, db: AsyncSession) -> Project:
    """Create a new project: ensure user GCP resources -> Docker container -> DB record."""
    project_id = uuid.uuid4()
    logger.info("Creating project %s '%s' for user %s", project_id, name, user_id)

    # Fetch user and ensure GCP resources
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    await _ensure_user_gcp_resources(user, db)

    gcs_prefix = f"{project_id}/workspace"
    ssh_public_key, ssh_private_key = _generate_ssh_keypair()
    logger.info("[%s] SSH keypair generated", project_id)

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
    logger.info("[%s] DB record created (status=creating)", project_id)

    try:
        # Create Docker container
        logger.info("[%s] Creating Docker container (image=%s)...", project_id, SANDBOX_IMAGE)
        config = {
            "image": SANDBOX_IMAGE,
            "gcs_bucket": user.gcs_bucket,
            "gcs_sa_key": user.gcp_sa_key,
            "ssh_public_key": ssh_public_key,
        }
        container_id, ssh_port = await asyncio.to_thread(
            docker_mgr.create_container, str(project_id), config,
        )
        logger.info("[%s] Container created: %s (SSH port %d)", project_id, container_id[:12], ssh_port)

        await asyncio.to_thread(docker_mgr.connect_proxy_to_network, str(project_id))
        logger.info("[%s] Terminal proxy connected to sandbox network", project_id)

        project.container_id = container_id
        project.container_name = f"sandbox-{project_id}"
        project.volume_name = f"vol-{project_id}"
        project.ssh_host_port = ssh_port
        project.status = "running"

        await db.commit()
        await db.refresh(project)
        logger.info("[%s] Project ready (status=running)", project_id)
        return project

    except Exception as e:
        logger.error("[%s] Failed to create project: %s", project_id, e)
        await _cleanup_failed_create(project, db)
        raise


async def _cleanup_failed_create(project: Project, db: AsyncSession):
    """Clean up resources from a failed project creation.

    Only cleans Docker resources. Does NOT delete user SA/bucket.
    """
    logger.info("[%s] Cleaning up failed create...", project.id)
    try:
        await asyncio.to_thread(docker_mgr.cleanup_project_resources, str(project.id))
    except Exception as e:
        logger.warning("[%s] Docker cleanup error (ignored): %s", project.id, e)
    project.status = "error"
    await db.commit()
    logger.info("[%s] Cleanup done (status=error)", project.id)


async def stop_project(project_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Project:
    """Stop a project: snapshot -> stop container."""
    project = await _get_owned_project(project_id, user_id, db)
    if project.status != "running":
        raise ValueError(f"Project is not running (status={project.status})")

    logger.info("[%s] Stopping project (snapshotting)...", project_id)
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
        logger.info("[%s] Snapshot complete, project stopped", project_id)
    except Exception as e:
        logger.error("[%s] Snapshot failed: %s", project_id, e)
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

    # Fetch user for GCP credentials
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    logger.info("[%s] Starting project (restoring)...", project_id)
    project.status = "restoring"
    await db.commit()

    try:
        config = {
            "gcs_bucket": user.gcs_bucket,
            "gcs_sa_key": user.gcp_sa_key,
            "ssh_public_key": project.ssh_public_key,
        }

        image = snapshot_mgr.restore_image_for_project(project.snapshot_image, SANDBOX_IMAGE)
        logger.info("[%s] Restoring from image: %s", project_id, image)

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

        await asyncio.to_thread(docker_mgr.connect_proxy_to_network, str(project_id))
        logger.info("[%s] Terminal proxy connected to sandbox network", project_id)
        logger.info("[%s] Project started (status=running)", project_id)
    except Exception as e:
        logger.error("[%s] Restore failed: %s", project_id, e)
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
    """Delete a project: Docker teardown + GCS prefix cleanup. Does NOT delete user SA/bucket."""
    project = await _get_owned_project(project_id, user_id, db)
    logger.info("[%s] Deleting project...", project_id)

    # Fetch user for bucket name
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    logger.info("[%s] Disconnecting terminal proxy from sandbox network...", project_id)
    await asyncio.to_thread(docker_mgr.disconnect_proxy_from_network, str(project_id))

    logger.info("[%s] Cleaning up Docker resources...", project_id)
    await asyncio.to_thread(docker_mgr.cleanup_project_resources, str(project_id))

    if user.gcs_bucket:
        logger.info("[%s] Deleting GCS prefix %s/ from bucket %s...", project_id, project_id, user.gcs_bucket)
        await asyncio.to_thread(
            gcp_iam.delete_gcs_prefix,
            user.gcs_bucket, f"{project_id}/", GCP_PROJECT, CREDENTIALS_PATH,
        )

    logger.info("[%s] Deleting snapshot images...", project_id)
    await asyncio.to_thread(snapshot_mgr.delete_snapshot_images, str(project_id))

    await db.delete(project)
    await db.commit()
    logger.info("[%s] Project deleted", project_id)


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
