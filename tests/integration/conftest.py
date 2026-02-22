"""
Integration Test Fixtures
M2: GCS storage, Docker container, and cleanup helpers.
M3: GCP IAM service account lifecycle testing.
"""

import json
import os
import subprocess
import time
import uuid

import pytest
from google.cloud import storage

try:
    import docker
except ImportError:
    docker = None  # M3 tests don't need Docker

# ---------------------------------------------------------------------------
# Configuration — override via env vars or use defaults for pomodex project
# ---------------------------------------------------------------------------

GCS_BUCKET = os.environ.get("GCS_BUCKET", "pomodex-fd2bcd-sandbox")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "pomodex-fd2bcd")
GCS_KEY_PATH = os.environ.get(
    "GCS_KEY_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "secrets", "gcs-test-key.json"),
)
IMAGE_NAME = os.environ.get("SANDBOX_IMAGE", "agent-sandbox:test")
CONTAINER_PREFIX = "m2-test"


# ---------------------------------------------------------------------------
# GCS fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gcs_client():
    """Authenticated GCS client using test SA key."""
    return storage.Client.from_service_account_json(GCS_KEY_PATH, project=GCP_PROJECT)


@pytest.fixture(scope="session")
def gcs_bucket(gcs_client):
    """The GCS bucket object."""
    return gcs_client.bucket(GCS_BUCKET)


@pytest.fixture(scope="session")
def gcs_sa_key():
    """Raw SA key JSON string for injecting into containers."""
    with open(GCS_KEY_PATH) as f:
        return f.read()


@pytest.fixture(scope="session")
def project_id():
    """Unique project ID per test session to avoid collisions."""
    return f"test-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Docker fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_client():
    """Docker client from environment."""
    return docker.from_env()


@pytest.fixture(scope="session")
def sandbox_image(docker_client):
    """Build the sandbox image before tests run."""
    build_dir = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "sandbox")
    build_dir = os.path.abspath(build_dir)
    image, _logs = docker_client.images.build(path=build_dir, tag=IMAGE_NAME, rm=True)
    return image


@pytest.fixture()
def sandbox_container(docker_client, sandbox_image, gcs_sa_key, project_id):
    """
    Run a sandbox container with real GCS credentials.
    Yields the container object. Cleans up after test.
    """
    name = f"{CONTAINER_PREFIX}-{uuid.uuid4().hex[:6]}"
    container = docker_client.containers.run(
        IMAGE_NAME,
        detach=True,
        name=name,
        cap_add=["SYS_ADMIN"],
        devices=["/dev/fuse"],
        environment={
            "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
            "GCS_SA_KEY": gcs_sa_key,
            "PROJECT_ID": project_id,
            "GCS_BUCKET": GCS_BUCKET,
        },
    )
    # Wait for container to be running and entrypoint to finish
    _wait_container_ready(container)
    yield container
    container.remove(force=True)


@pytest.fixture()
def sandbox_container_fast_backup(docker_client, sandbox_image, gcs_sa_key, project_id):
    """
    Sandbox container with backup interval set to 10s for faster test cycles.
    """
    name = f"{CONTAINER_PREFIX}-backup-{uuid.uuid4().hex[:6]}"
    container = docker_client.containers.run(
        IMAGE_NAME,
        detach=True,
        name=name,
        cap_add=["SYS_ADMIN"],
        devices=["/dev/fuse"],
        environment={
            "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
            "GCS_SA_KEY": gcs_sa_key,
            "PROJECT_ID": project_id,
            "GCS_BUCKET": GCS_BUCKET,
            "BACKUP_INTERVAL_SECONDS": "10",
        },
    )
    _wait_container_ready(container)
    yield container
    container.remove(force=True)


@pytest.fixture()
def fresh_volume_container(docker_client, sandbox_image, gcs_sa_key, project_id):
    """
    Sandbox container with a fresh named volume (no .sandbox_initialized).
    """
    vol_name = f"{CONTAINER_PREFIX}-vol-{uuid.uuid4().hex[:6]}"
    volume = docker_client.volumes.create(name=vol_name)
    name = f"{CONTAINER_PREFIX}-fresh-{uuid.uuid4().hex[:6]}"
    container = docker_client.containers.run(
        IMAGE_NAME,
        detach=True,
        name=name,
        cap_add=["SYS_ADMIN"],
        devices=["/dev/fuse"],
        volumes={vol_name: {"bind": "/home/agent", "mode": "rw"}},
        environment={
            "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
            "GCS_SA_KEY": gcs_sa_key,
            "PROJECT_ID": project_id,
            "GCS_BUCKET": GCS_BUCKET,
        },
    )
    _wait_container_ready(container)
    yield container, volume
    container.remove(force=True)
    volume.remove(force=True)


# ---------------------------------------------------------------------------
# GCS cleanup fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cleanup_gcs_test_prefix(gcs_client, project_id):
    """Clean up GCS objects created during tests after each test."""
    yield
    bucket = gcs_client.bucket(GCS_BUCKET)
    blobs = list(bucket.list_blobs(prefix=f"projects/{project_id}/"))
    for blob in blobs:
        blob.delete()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_container_ready(container, timeout=30):
    """Wait for container to be running and supervisord to start."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        container.reload()
        if container.status == "running":
            # Check if supervisord is responding
            try:
                exit_code, _ = container.exec_run("supervisorctl status")
                if exit_code == 0:
                    return
            except Exception:
                pass
        time.sleep(1)
    raise TimeoutError(f"Container {container.name} not ready after {timeout}s")


# ---------------------------------------------------------------------------
# M3: IAM fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gcp_project():
    return GCP_PROJECT


@pytest.fixture(scope="session")
def gcs_bucket_name():
    return GCS_BUCKET


@pytest.fixture(scope="session")
def sa_key_path():
    return GCS_KEY_PATH


@pytest.fixture(scope="session")
def test_project_id():
    """Unique project ID for this M3 test session."""
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def iam_client():
    """IAM admin client authenticated with the Project Service SA."""
    from google.cloud import iam_admin_v1
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(GCS_KEY_PATH)
    return iam_admin_v1.IAMClient(credentials=credentials)


# ---------------------------------------------------------------------------
# M3: SA cleanup tracker
# ---------------------------------------------------------------------------

_created_sa_emails = []


@pytest.fixture(scope="session")
def created_sa_tracker():
    """Track created SA emails for cleanup."""
    return _created_sa_emails


@pytest.fixture(scope="session", autouse=True)
def cleanup_created_sas(gcp_project):
    """Delete all SAs created during the test session, including IAM bindings."""
    yield
    from backend.project_service.services.gcp_iam import delete_service_account

    for email in _created_sa_emails:
        try:
            delete_service_account(
                email, gcp_project,
                credentials_path=GCS_KEY_PATH,
                bucket=GCS_BUCKET,
            )
        except Exception:
            pass  # Best-effort cleanup
