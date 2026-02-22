"""Integration tests for snapshot & restore (M5).
Tests T5.1–T5.8, T5.10, T5.12, T5.13.
"""

import subprocess
import time
import uuid

import docker
import pytest

from backend.project_service.services.docker_manager import (
    cleanup_project_resources,
    create_container,
    create_volume,
    delete_container,
    delete_volume,
    stop_container,
)
from backend.project_service.services.snapshot_manager import (
    AR_REGISTRY,
    delete_snapshot_images,
    restore_from_gcs,
    restore_from_snapshot,
    snapshot_project,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

IMAGE_NAME = "agent-sandbox:test"
GCS_BUCKET = "pomodex-fd2bcd-sandbox"


@pytest.fixture()
def m5_project_id():
    """Unique project ID for each M5 test."""
    return f"m5-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def m5_cleanup(docker_client):
    """Track and clean up Docker + AR resources created during M5 tests."""
    project_ids = []
    yield project_ids
    for pid in project_ids:
        # Clean up Docker resources
        for name, getter, force in [
            (f"sandbox-{pid}", docker_client.containers, True),
            (f"vol-{pid}", docker_client.volumes, True),
            (f"net-{pid}", docker_client.networks, False),
        ]:
            try:
                obj = getter.get(name)
                if force:
                    obj.remove(force=True)
                else:
                    obj.remove()
            except Exception:
                pass
        # Clean up any committed test images
        for img_tag in [f"{AR_REGISTRY}/{pid}:latest", f"{AR_REGISTRY}/{pid}"]:
            try:
                docker_client.images.remove(img_tag, force=True)
            except Exception:
                pass
        # Clean up AR images (best-effort)
        try:
            delete_snapshot_images(pid)
        except Exception:
            pass


@pytest.fixture()
def m5_running_container(docker_client, sandbox_image, m5_project_id, m5_cleanup, gcs_sa_key):
    """Create a running sandbox container for M5 tests. Returns (container_id, project_id)."""
    m5_cleanup.append(m5_project_id)
    config = {
        "image": IMAGE_NAME,
        "gcs_bucket": GCS_BUCKET,
        "gcs_sa_key": gcs_sa_key,
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
    }
    container_id, ssh_port = create_container(m5_project_id, config)
    # Wait for container to be ready
    container = docker_client.containers.get(container_id)
    _wait_ready(container)
    return container_id, m5_project_id


def _wait_ready(container, timeout=30):
    """Wait for container to be running and supervisord responding."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        container.reload()
        if container.status == "running":
            try:
                exit_code, _ = container.exec_run("supervisorctl status")
                if exit_code == 0:
                    return
            except Exception:
                pass
        time.sleep(1)
    raise TimeoutError(f"Container {container.name} not ready after {timeout}s")


# ---------------------------------------------------------------------------
# T5.1: Docker commit creates image from running container
# ---------------------------------------------------------------------------


class TestDockerCommit:
    """T5.1: docker commit captures filesystem state."""

    def test_committed_image_preserves_installed_package(
        self, docker_client, m5_running_container
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Install cowsay inside the container
        exit_code, output = container.exec_run(
            ["bash", "-c", "apt-get update && apt-get install -y cowsay"],
            user="root",
        )
        assert exit_code == 0, f"cowsay install failed: {output.decode()}"

        # Commit the container
        committed = container.commit(
            repository=f"m5-test-commit-{project_id}",
            tag="test",
        )

        # Start a new container from the committed image
        new_container = docker_client.containers.run(
            committed.id,
            detach=True,
            name=f"m5-verify-{project_id}",
            command="sleep 30",
        )
        try:
            time.sleep(2)
            exit_code, output = new_container.exec_run(
                ["bash", "-c", "which cowsay || test -f /usr/games/cowsay"],
                user="root",
            )
            assert exit_code == 0, f"cowsay not found in committed image: {output.decode()}"
        finally:
            new_container.remove(force=True)
            docker_client.images.remove(committed.id, force=True)

    def test_committed_image_has_correct_tag(
        self, docker_client, m5_running_container
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        tag_name = f"m5-test-tag-{project_id}"
        committed = container.commit(repository=tag_name, tag="v1")

        try:
            assert any(
                f"{tag_name}:v1" in (t or "")
                for t in committed.tags
            )
        finally:
            docker_client.images.remove(committed.id, force=True)


# ---------------------------------------------------------------------------
# T5.2: Committed image does NOT contain volume data
# ---------------------------------------------------------------------------


class TestVolumeExclusion:
    """T5.2: docker commit excludes volume-mounted paths."""

    def test_volume_data_not_in_committed_image(
        self, docker_client, m5_running_container
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Write a file to /home/agent (volume mount point)
        exit_code, _ = container.exec_run(
            "bash -c 'echo volume-only-data > /home/agent/volume-only.txt'",
            user="agent",
        )
        assert exit_code == 0

        # Commit the container
        committed = container.commit(
            repository=f"m5-test-vol-{project_id}",
            tag="test",
        )

        # Start new container from committed image WITHOUT the volume
        new_container = docker_client.containers.run(
            committed.id,
            detach=True,
            name=f"m5-vol-verify-{project_id}",
            command="sleep 30",
        )
        try:
            time.sleep(2)
            exit_code, output = new_container.exec_run(
                "cat /home/agent/volume-only.txt",
                user="root",
            )
            # File should NOT exist — volume data is not captured by commit
            assert exit_code != 0, "Volume data was found in committed image — should not be"
        finally:
            new_container.remove(force=True)
            docker_client.images.remove(committed.id, force=True)


# ---------------------------------------------------------------------------
# T5.3: Push to Artifact Registry with correct tags
# ---------------------------------------------------------------------------


class TestARPush:
    """T5.3: Push snapshot to AR with timestamp + latest tags."""

    def test_push_both_tags_to_ar(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container
        result = snapshot_project(project_id, sa_key_path=sa_key_path)

        # Verify both tags exist in AR
        ar_image = f"{AR_REGISTRY}/{project_id}"
        output = subprocess.check_output(
            [
                "gcloud", "artifacts", "docker", "images", "list",
                ar_image,
                "--project=pomodex-fd2bcd",
                "--include-tags",
                "--format=json",
            ],
            text=True,
        )
        import json
        images = json.loads(output)
        assert len(images) > 0, "No images found in AR"

        # Collect all tags
        all_tags = []
        for img in images:
            tags = img.get("tags", [])
            if isinstance(tags, list):
                all_tags.extend(tags)
            elif isinstance(tags, str) and tags:
                all_tags.extend(t.strip() for t in tags.split(","))
        all_tags = [t.strip() for t in all_tags if t.strip()]

        assert "latest" in all_tags, f"'latest' tag not found. Tags: {all_tags}"
        # Should have at least one timestamp tag
        ts_tags = [t for t in all_tags if t != "latest"]
        assert len(ts_tags) >= 1, f"No timestamp tag found. Tags: {all_tags}"


# ---------------------------------------------------------------------------
# T5.4: Pull snapshot image from Artifact Registry
# ---------------------------------------------------------------------------


class TestARPull:
    """T5.4: Pull snapshot image after removing local copy."""

    def test_pull_after_local_removal(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container
        result = snapshot_project(project_id, sa_key_path=sa_key_path)
        image_ref = result["snapshot_image"]

        # Get the digest before removing
        local_img = docker_client.images.get(image_ref)
        original_digest = local_img.id

        # Remove local copies
        for tag in docker_client.images.get(image_ref).tags:
            try:
                docker_client.images.remove(tag, force=True)
            except Exception:
                pass

        # Pull using latest tag
        pulled = docker_client.images.pull(image_ref)
        assert pulled.id == original_digest, "Pulled image digest doesn't match original"


# ---------------------------------------------------------------------------
# T5.5: Fast restore — snapshot image + existing volume
# ---------------------------------------------------------------------------


class TestFastRestore:
    """T5.5: Restore from snapshot image with existing volume."""

    def test_fast_restore_preserves_image_and_volume_state(
        self, docker_client, m5_running_container, sa_key_path, gcs_sa_key
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Install a package (image state)
        exit_code, _ = container.exec_run(
            ["bash", "-c", "apt-get update && apt-get install -y cowsay"],
            user="root",
        )
        assert exit_code == 0

        # Write a file to volume
        exit_code, _ = container.exec_run(
            "bash -c 'echo fast-restore-data > /home/agent/data.txt'",
            user="agent",
        )
        assert exit_code == 0

        # Snapshot
        result = snapshot_project(project_id, sa_key_path=sa_key_path)

        # Stop + remove container (keep volume)
        delete_container(project_id)

        # Restore from snapshot
        config = {
            "gcs_bucket": GCS_BUCKET,
            "gcs_sa_key": gcs_sa_key,
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
        }
        new_container_id = restore_from_snapshot(
            project_id,
            snapshot_image=result["snapshot_image"],
            config=config,
        )
        new_container = docker_client.containers.get(new_container_id)
        _wait_ready(new_container)

        # Verify package from image
        exit_code, _ = new_container.exec_run(
            ["bash", "-c", "which cowsay || test -f /usr/games/cowsay"],
            user="root",
        )
        assert exit_code == 0, "cowsay not found after fast restore"

        # Verify file from volume
        exit_code, output = new_container.exec_run(
            "cat /home/agent/data.txt",
            user="agent",
        )
        assert exit_code == 0
        assert b"fast-restore-data" in output


# ---------------------------------------------------------------------------
# T5.6: Fallback restore — base image + GCS restore
# ---------------------------------------------------------------------------


class TestFallbackRestore:
    """T5.6: Restore from base image + GCS when volume is lost."""

    def test_fallback_restore_recovers_from_gcs(
        self, docker_client, m5_running_container, sa_key_path, gcs_sa_key, gcs_bucket
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Write a file and wait for backup
        exit_code, _ = container.exec_run(
            "bash -c 'echo fallback-test-data > /home/agent/fallback-file.txt'",
            user="agent",
        )
        assert exit_code == 0

        # Trigger rclone sync manually to ensure data is in GCS
        exit_code, output = container.exec_run(
            [
                "rclone", "sync", "/home/agent",
                f":gcs:{GCS_BUCKET}/projects/{project_id}/workspace",
                "--transfers=8", "--checksum",
                "--gcs-service-account-file=/tmp/gcs-key.json",
                "--gcs-bucket-policy-only",
            ],
            user="root",
        )
        assert exit_code == 0, f"rclone sync failed: {output.decode()}"

        # Verify file is in GCS
        blob = gcs_bucket.blob(f"projects/{project_id}/workspace/fallback-file.txt")
        assert blob.exists(), "File not in GCS after sync"

        # Delete container AND volume (disaster scenario)
        cleanup_project_resources(project_id)

        # Restore using base image + fresh volume
        config = {
            "gcs_bucket": GCS_BUCKET,
            "gcs_sa_key": gcs_sa_key,
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
        }
        new_container_id = restore_from_gcs(
            project_id,
            base_image=IMAGE_NAME,
            config=config,
        )
        new_container = docker_client.containers.get(new_container_id)
        _wait_ready(new_container, timeout=60)

        # Wait for entrypoint GCS restore to complete
        time.sleep(10)

        # Check logs for restore messages
        logs = new_container.logs().decode()
        assert "First boot: checking for GCS backup..." in logs
        assert "Backup found" in logs

        # Verify file restored from GCS
        exit_code, output = new_container.exec_run(
            "cat /home/agent/fallback-file.txt",
            user="agent",
        )
        assert exit_code == 0, f"File not restored. Logs:\n{logs}"
        assert b"fallback-test-data" in output


# ---------------------------------------------------------------------------
# T5.7: Final rclone sync runs before commit
# ---------------------------------------------------------------------------


class TestPreSnapshotSync:
    """T5.7: rclone sync runs before docker commit."""

    def test_last_minute_file_synced_to_gcs(
        self, docker_client, m5_running_container, sa_key_path, gcs_bucket
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Create a file right before snapshot
        exit_code, _ = container.exec_run(
            "bash -c 'echo last-minute-content > /home/agent/last-minute.txt'",
            user="agent",
        )
        assert exit_code == 0

        # Trigger snapshot (runs rclone sync first)
        snapshot_project(project_id, sa_key_path=sa_key_path)

        # Check GCS for the file
        blob = gcs_bucket.blob(f"projects/{project_id}/workspace/last-minute.txt")
        assert blob.exists(), "last-minute.txt not found in GCS after snapshot sync"
        content = blob.download_as_text()
        assert "last-minute-content" in content


# ---------------------------------------------------------------------------
# T5.8: Snapshot returns correct metadata
# ---------------------------------------------------------------------------


class TestSnapshotMetadata:
    """T5.8: snapshot_project returns correct metadata dict."""

    def test_returns_correct_metadata(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container

        before = time.time()
        result = snapshot_project(project_id, sa_key_path=sa_key_path)
        after = time.time()

        assert result["snapshot_image"] == f"{AR_REGISTRY}/{project_id}:latest"
        assert result["status"] == "stopped"

        # last_snapshot_at should be a timestamp within our window
        ts = result["last_snapshot_at"]
        assert before <= ts <= after, f"Timestamp {ts} not in [{before}, {after}]"


# ---------------------------------------------------------------------------
# T5.10: Delete snapshot images from AR
# ---------------------------------------------------------------------------


class TestDeleteSnapshots:
    """T5.10: delete_snapshot_images removes all tags for a project."""

    def test_deletes_all_images_for_project(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container

        # Create a snapshot (pushes to AR)
        snapshot_project(project_id, sa_key_path=sa_key_path)

        # Delete all snapshot images
        delete_snapshot_images(project_id)

        # Verify no images remain
        output = subprocess.check_output(
            [
                "gcloud", "artifacts", "docker", "images", "list",
                f"{AR_REGISTRY}/{project_id}",
                "--project=pomodex-fd2bcd",
                "--format=json",
            ],
            text=True,
        )
        import json
        images = json.loads(output)
        assert len(images) == 0, f"Images still exist after delete: {images}"


# ---------------------------------------------------------------------------
# T5.12: Snapshot performance
# ---------------------------------------------------------------------------


class TestSnapshotPerformance:
    """T5.12: Snapshot (commit + push) completes within 1 minute."""

    def test_snapshot_under_60_seconds(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container
        container = docker_client.containers.get(container_id)

        # Install some packages to make the image non-trivial
        container.exec_run(
            ["bash", "-c", "apt-get update && apt-get install -y vim nano htop tree"],
            user="root",
        )

        start = time.time()
        result = snapshot_project(project_id, sa_key_path=sa_key_path)
        elapsed = time.time() - start

        print(f"\n--- Snapshot Performance ---")
        print(f"Total time: {elapsed:.1f}s")
        print(f"Image: {result['snapshot_image']}")

        assert elapsed < 60, f"Snapshot took {elapsed:.1f}s (limit: 60s)"


# ---------------------------------------------------------------------------
# T5.13: Container stop after snapshot
# ---------------------------------------------------------------------------


class TestContainerStopAfterSnapshot:
    """T5.13: Container is stopped and removed after snapshot, volume remains."""

    def test_container_removed_volume_remains(
        self, docker_client, m5_running_container, sa_key_path
    ):
        container_id, project_id = m5_running_container

        result = snapshot_project(project_id, sa_key_path=sa_key_path)

        # Container should be gone
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(f"sandbox-{project_id}")

        # Volume should still exist
        volume = docker_client.volumes.get(f"vol-{project_id}")
        assert volume.name == f"vol-{project_id}"

        # Metadata confirms stopped
        assert result["status"] == "stopped"
