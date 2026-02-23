"""
Snapshot & restore management for sandbox containers.

Handles docker commit, Artifact Registry push/pull, and container
restoration from either snapshot images or GCS backups.
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone

import docker
from docker.errors import APIError, NotFound

logger = logging.getLogger(__name__)

AR_REGISTRY = "europe-west1-docker.pkg.dev/pomodex-fd2bcd/sandboxes"
GCS_KEY_PATH_DEFAULT = "secrets/gcs-test-key.json"


def _get_client() -> docker.DockerClient:
    """Get a Docker client from environment."""
    return docker.from_env()


def _push_image(client: docker.DockerClient, image: str, tag: str, auth_config: dict) -> None:
    """Push an image to AR and raise on failure."""
    logger.info("Pushing %s:%s to AR", image, tag)
    output = client.images.push(image, tag=tag, auth_config=auth_config)

    # Docker SDK returns newline-delimited JSON; last line has the result
    for line in output.strip().splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "error" in msg:
            raise RuntimeError(f"Push failed for {image}:{tag}: {msg['error']}")

    logger.info("Pushed %s:%s to AR", image, tag)


def _ar_auth_config(sa_key_path: str) -> dict:
    """Build auth_config dict for Artifact Registry using SA key JSON.

    Returns a dict that can be passed directly to Docker SDK pull()/push()
    as the auth_config parameter. This avoids relying on daemon-level
    credential persistence across client instances.
    """
    with open(sa_key_path) as f:
        key_json = f.read()

    return {"username": "_json_key", "password": key_json}


def restore_image_for_project(snapshot_image: str | None, base_image: str) -> str:
    """Determine which image to use for restoring a project.

    Returns the snapshot image if available, otherwise the base image.
    """
    if snapshot_image:
        return snapshot_image
    return base_image


def snapshot_project(project_id: str, sa_key_path: str = GCS_KEY_PATH_DEFAULT) -> dict:
    """Snapshot a running container: rclone sync, docker commit, push to AR.

    Steps:
    1. Run final rclone sync inside the container
    2. docker commit the container
    3. Tag with timestamp + latest
    4. Push both tags to Artifact Registry
    5. Stop and remove the container (volume preserved)

    Returns metadata dict:
        snapshot_image: str  - AR image ref (registry/project_id:latest)
        last_snapshot_at: float - Unix timestamp
        status: str - "stopped"
    """
    client = _get_client()
    container_name = f"sandbox-{project_id}"
    container = client.containers.get(container_name)

    # 1. Final rclone sync
    logger.info("Running final rclone sync for %s", project_id)
    env = dict(e.split("=", 1) for e in container.attrs["Config"]["Env"])
    gcs_bucket = env.get("GCS_BUCKET", "")
    exit_code, output = container.exec_run(
        [
            "rclone", "sync", "/home/agent",
            f":gcs:{gcs_bucket}/{project_id}/workspace",
            "--transfers=8", "--checksum",
            "--gcs-service-account-file=/tmp/gcs-key.json",
            "--gcs-bucket-policy-only",
        ],
        user="root",
    )
    if exit_code != 0:
        logger.warning("rclone sync returned %d: %s", exit_code, output.decode())

    # 2. docker commit
    logger.info("Committing container %s", container_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ar_image = f"{AR_REGISTRY}/{project_id}"

    committed = container.commit(repository=ar_image, tag=timestamp)
    # Also tag as latest
    committed.tag(ar_image, tag="latest")

    # 3. Authenticate and push to AR
    auth_config = _ar_auth_config(sa_key_path)
    _push_image(client, ar_image, timestamp, auth_config)
    _push_image(client, ar_image, "latest", auth_config)

    # 4. Stop and remove container (keep volume)
    logger.info("Stopping and removing container %s", container_name)
    container.stop(timeout=30)
    container.remove()

    snapshot_at = time.time()

    return {
        "snapshot_image": f"{ar_image}:latest",
        "last_snapshot_at": snapshot_at,
        "status": "stopped",
    }


def restore_from_snapshot(
    project_id: str,
    snapshot_image: str,
    config: dict,
    sa_key_path: str = GCS_KEY_PATH_DEFAULT,
) -> str:
    """Restore a container from a snapshot image with existing volume.

    Pulls the snapshot image from AR and creates a new container using
    the existing volume (vol-{project_id}).

    config keys:
        gcs_bucket: str
        gcs_sa_key: str
        ssh_public_key: str

    Returns the new container ID.
    """
    client = _get_client()

    # Use local image if available, otherwise pull from AR
    try:
        client.images.get(snapshot_image)
        logger.info("Using local snapshot image %s", snapshot_image)
    except docker.errors.ImageNotFound:
        auth_config = _ar_auth_config(sa_key_path)
        logger.info("Pulling snapshot image %s from AR", snapshot_image)
        client.images.pull(snapshot_image, auth_config=auth_config)
        logger.info("Pulled snapshot image %s", snapshot_image)

    # Create container from snapshot image with existing volume
    container = client.containers.run(
        image=snapshot_image,
        name=f"sandbox-{project_id}",
        detach=True,
        volumes={
            f"vol-{project_id}": {"bind": "/home/agent", "mode": "rw"},
        },
        ports={"22/tcp": None},  # Auto-assign port
        environment={
            "PROJECT_ID": str(project_id),
            "GCS_BUCKET": config["gcs_bucket"],
            "GCS_PREFIX": str(project_id),
            "GCS_SA_KEY": config["gcs_sa_key"],
            "SSH_PUBLIC_KEY": config["ssh_public_key"],
        },
        network=f"net-{project_id}",
        cap_add=["SYS_ADMIN"],
        devices=["/dev/fuse"],
        security_opt=["apparmor:unconfined"],
        mem_limit="1g",
        nano_cpus=1_000_000_000,
    )
    return container.id


def restore_from_gcs(
    project_id: str,
    base_image: str,
    config: dict,
) -> str:
    """Restore a container from base image with GCS backup restore.

    Creates fresh volume + container. The entrypoint handles GCS restore
    on first boot (no .sandbox_initialized flag).

    config keys:
        gcs_bucket: str
        gcs_sa_key: str
        ssh_public_key: str

    Returns the new container ID.
    """
    from backend.project_service.services.docker_manager import (
        create_network,
        create_volume,
    )

    client = _get_client()

    # Create fresh network + volume
    try:
        create_network(project_id)
    except Exception:
        pass  # Network may already exist

    create_volume(project_id)

    # Create container from base image — entrypoint does GCS restore
    container = client.containers.run(
        image=base_image,
        name=f"sandbox-{project_id}",
        detach=True,
        volumes={
            f"vol-{project_id}": {"bind": "/home/agent", "mode": "rw"},
        },
        ports={"22/tcp": None},
        environment={
            "PROJECT_ID": str(project_id),
            "GCS_BUCKET": config["gcs_bucket"],
            "GCS_PREFIX": str(project_id),
            "GCS_SA_KEY": config["gcs_sa_key"],
            "SSH_PUBLIC_KEY": config["ssh_public_key"],
        },
        network=f"net-{project_id}",
        cap_add=["SYS_ADMIN"],
        devices=["/dev/fuse"],
        security_opt=["apparmor:unconfined"],
        mem_limit="1g",
        nano_cpus=1_000_000_000,
    )
    return container.id


def list_snapshots(project_id: str) -> list[dict]:
    """List all snapshot tags for a project from Artifact Registry.

    Returns sorted list (newest first) of {tag, created_at} dicts.
    Excludes the 'latest' tag.
    """
    ar_image = f"{AR_REGISTRY}/{project_id}"

    try:
        output = subprocess.check_output(
            [
                "gcloud", "artifacts", "docker", "images", "list",
                ar_image,
                "--project=pomodex-fd2bcd",
                "--format=json",
                "--include-tags",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return []

    images = json.loads(output) if output.strip() else []
    if not images:
        return []

    snapshots = []
    for img in images:
        tags = img.get("tags", "")
        # tags can be a comma-separated string
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if isinstance(tags, str) else tags
        for tag in tag_list:
            if tag == "latest":
                continue
            # Parse timestamp tag format: YYYYMMDD-HHMMSS
            try:
                created_at = datetime.strptime(tag, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
                snapshots.append({"tag": tag, "created_at": created_at})
            except ValueError:
                continue

    # Sort newest first
    snapshots.sort(key=lambda s: s["created_at"], reverse=True)
    return snapshots


def delete_snapshot_images(project_id: str) -> None:
    """Delete all snapshot images for a project from Artifact Registry.

    Uses gcloud CLI to list and delete all images under the project's
    AR path.
    """
    ar_image = f"{AR_REGISTRY}/{project_id}"

    # List all image digests for this project
    try:
        output = subprocess.check_output(
            [
                "gcloud", "artifacts", "docker", "images", "list",
                ar_image,
                "--project=pomodex-fd2bcd",
                "--format=json",
                "--include-tags",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        logger.info("No images found for %s (already clean)", project_id)
        return

    images = json.loads(output) if output.strip() else []
    if not images:
        return

    # Delete each image by its full package path (includes digest)
    for img in images:
        # The "package" field is the full AR path, "version" is the digest
        version = img.get("version", "")
        if not version:
            continue
        image_ref = f"{ar_image}@{version}"
        try:
            subprocess.check_call(
                [
                    "gcloud", "artifacts", "docker", "images", "delete",
                    image_ref,
                    "--project=pomodex-fd2bcd",
                    "--quiet",
                    "--delete-tags",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Deleted %s", image_ref)
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to delete %s: %s", image_ref, e)

    # Also clean up local images
    client = _get_client()
    for tag in ["latest", ""]:
        ref = f"{ar_image}:{tag}" if tag else ar_image
        try:
            client.images.remove(ref, force=True)
        except Exception:
            pass
