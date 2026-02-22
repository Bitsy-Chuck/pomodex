"""
M2: GCS Storage & Backup System — Integration Tests
All 11 test cases from M2-gcs-storage-and-backup.md

Requires:
  - Docker running with agent-sandbox:test image
  - Valid GCS SA key at secrets/gcs-test-key.json
  - GCS bucket pomodex-fd2bcd-sandbox with lifecycle rule
"""

import json
import time

import pytest
from google.cloud import storage

from .conftest import GCS_BUCKET, GCS_KEY_PATH, GCP_PROJECT


# =========================================================================
# T2.1: GCS bucket exists with correct lifecycle
# =========================================================================


class TestT21BucketLifecycle:
    def test_bucket_exists(self, gcs_client):
        bucket = gcs_client.bucket(GCS_BUCKET)
        assert bucket.exists(), f"Bucket {GCS_BUCKET} does not exist"

    def test_lifecycle_rule_delete_30_days(self, gcs_client):
        bucket = gcs_client.get_bucket(GCS_BUCKET)
        rules = list(bucket.lifecycle_rules)
        assert len(rules) >= 1, "No lifecycle rules found"
        delete_rules = [r for r in rules if r["action"]["type"] == "Delete"]
        assert len(delete_rules) >= 1, "No Delete lifecycle rule found"
        assert delete_rules[0]["condition"]["age"] == 30, (
            f"Delete rule age is {delete_rules[0]['condition']['age']}, expected 30"
        )


# =========================================================================
# T2.2: gcsfuse project mount (read-write)
# =========================================================================


class TestT22GcsfuseProjectMount:
    def test_mount_is_accessible(self, sandbox_container):
        exit_code, output = sandbox_container.exec_run("ls /mnt/gcs")
        assert exit_code == 0, f"Cannot list /mnt/gcs: {output.decode()}"

    def test_write_file_to_mount(self, sandbox_container):
        exit_code, output = sandbox_container.exec_run(
            "bash -c 'echo test-content > /mnt/gcs/test-file.txt'",
            user="agent",
        )
        assert exit_code == 0, f"Write failed: {output.decode()}"

    def test_file_appears_in_gcs(self, sandbox_container, gcs_client, project_id):
        # Write a file via the mount
        sandbox_container.exec_run(
            "bash -c 'echo gcs-check > /mnt/gcs/test-gcs-check.txt'",
            user="agent",
        )
        # gcsfuse may need a moment to sync
        time.sleep(3)
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"projects/{project_id}/test-gcs-check.txt")
        assert blob.exists(), (
            f"File not found in GCS at projects/{project_id}/test-gcs-check.txt"
        )

    def test_file_readable_back(self, sandbox_container):
        sandbox_container.exec_run(
            "bash -c 'echo readback-test > /mnt/gcs/readback.txt'",
            user="agent",
        )
        time.sleep(2)
        exit_code, output = sandbox_container.exec_run(
            "cat /mnt/gcs/readback.txt",
            user="agent",
        )
        assert exit_code == 0, f"Read failed: {output.decode()}"
        assert "readback-test" in output.decode()


# =========================================================================
# T2.3: gcsfuse shared mount (read-only)
# =========================================================================


class TestT23GcsfuseSharedMount:
    @pytest.fixture(autouse=True)
    def upload_shared_file(self, gcs_client):
        """Upload a test file to shared/ prefix before tests."""
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob("shared/readme.txt")
        blob.upload_from_string("Hello from shared storage")
        yield
        blob.delete()

    def test_shared_file_readable(self, sandbox_container):
        exit_code, output = sandbox_container.exec_run(
            "cat /mnt/shared/readme.txt",
            user="agent",
        )
        assert exit_code == 0, f"Cannot read shared file: {output.decode()}"
        assert "Hello from shared storage" in output.decode()

    def test_shared_mount_is_read_only(self, sandbox_container):
        exit_code, output = sandbox_container.exec_run(
            "touch /mnt/shared/new-file.txt",
            user="agent",
        )
        assert exit_code != 0, "Write to read-only mount should have failed"


# =========================================================================
# T2.4: Backup daemon syncs new files to GCS
# =========================================================================


class TestT24BackupSyncsNewFiles:
    def test_new_file_synced_to_gcs(
        self, sandbox_container_fast_backup, gcs_client, project_id
    ):
        container = sandbox_container_fast_backup
        # Create a file in /home/agent
        container.exec_run(
            "bash -c 'echo hello > /home/agent/test-backup.txt'",
            user="agent",
        )
        # Wait for backup daemon to run (interval=10s, give it 30s)
        time.sleep(30)
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"projects/{project_id}/workspace/test-backup.txt")
        assert blob.exists(), "Backup file not found in GCS"
        assert blob.download_as_text().strip() == "hello"

    def test_backup_daemon_logs_ok(self, sandbox_container_fast_backup):
        container = sandbox_container_fast_backup
        container.exec_run(
            "bash -c 'echo logtest > /home/agent/logtest.txt'",
            user="agent",
        )
        time.sleep(30)
        logs = container.logs().decode()
        assert "Backup OK" in logs, f"Expected 'Backup OK' in logs, got: {logs[-500:]}"


# =========================================================================
# T2.5: Backup daemon syncs file deletions
# =========================================================================


class TestT25BackupSyncsDeletions:
    def test_deleted_file_removed_from_gcs(
        self, sandbox_container_fast_backup, gcs_client, project_id
    ):
        container = sandbox_container_fast_backup
        # Create file and wait for backup
        container.exec_run(
            "bash -c 'echo delete-me > /home/agent/delete-test.txt'",
            user="agent",
        )
        time.sleep(30)
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"projects/{project_id}/workspace/delete-test.txt")
        assert blob.exists(), "File should exist in GCS before deletion test"

        # Delete the file
        container.exec_run("rm /home/agent/delete-test.txt", user="agent")
        # Wait for next backup cycle
        time.sleep(20)
        assert not blob.exists(), "File should be deleted from GCS after rclone sync"


# =========================================================================
# T2.6: Backup daemon handles errors gracefully
# =========================================================================


class TestT26BackupErrorHandling:
    def test_daemon_survives_bad_credentials(self, docker_client, sandbox_image):
        """Start container with invalid GCS credentials, verify daemon stays running."""
        import uuid

        name = f"m2-test-badcreds-{uuid.uuid4().hex[:6]}"
        container = docker_client.containers.run(
            "agent-sandbox:test",
            detach=True,
            name=name,
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            environment={
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test",
                "GCS_SA_KEY": '{"type":"service_account","project_id":"bad"}',
                "PROJECT_ID": "bad-project",
                "GCS_BUCKET": "nonexistent-bucket",
                "BACKUP_INTERVAL_SECONDS": "5",
            },
        )
        try:
            time.sleep(20)
            container.reload()
            assert container.status == "running", "Container should still be running"

            logs = container.logs().decode()
            assert "Backup failed" in logs, "Expected error log from backup daemon"

            exit_code, output = container.exec_run("supervisorctl status backup-daemon")
            assert "RUNNING" in output.decode(), (
                f"backup-daemon should be RUNNING, got: {output.decode()}"
            )
        finally:
            container.remove(force=True)


# =========================================================================
# T2.7: First-boot restore from GCS
# =========================================================================


class TestT27FirstBootRestore:
    def test_restore_on_first_boot(
        self, docker_client, sandbox_image, gcs_client, gcs_sa_key, project_id
    ):
        """Upload files to GCS, start fresh container, verify restore."""
        import uuid

        # Upload test files to GCS workspace prefix
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"projects/{project_id}/workspace/restored-file.txt")
        blob.upload_from_string("restored content")

        # Create a fresh volume (no .sandbox_initialized)
        vol_name = f"m2-test-restore-{uuid.uuid4().hex[:6]}"
        volume = docker_client.volumes.create(name=vol_name)
        name = f"m2-test-restore-{uuid.uuid4().hex[:6]}"

        container = docker_client.containers.run(
            "agent-sandbox:test",
            detach=True,
            name=name,
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            volumes={vol_name: {"bind": "/home/agent", "mode": "rw"}},
            environment={
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test",
                "GCS_SA_KEY": gcs_sa_key,
                "PROJECT_ID": project_id,
                "GCS_BUCKET": GCS_BUCKET,
            },
        )
        try:
            time.sleep(15)
            logs = container.logs().decode()
            assert "First boot: checking for GCS backup" in logs
            assert "Backup found" in logs

            exit_code, output = container.exec_run(
                "cat /home/agent/restored-file.txt", user="agent"
            )
            assert exit_code == 0, f"Restored file not found: {output.decode()}"
            assert "restored content" in output.decode()

            exit_code, _ = container.exec_run(
                "test -f /home/agent/.sandbox_initialized"
            )
            assert exit_code == 0, ".sandbox_initialized flag not created"
        finally:
            container.remove(force=True)
            volume.remove(force=True)
            blob.delete()


# =========================================================================
# T2.8: First-boot skipped when flag exists
# =========================================================================


class TestT28FirstBootSkipped:
    def test_skip_restore_when_flag_exists(self, fresh_volume_container):
        """After first boot, restart container — should skip restore."""
        container, volume = fresh_volume_container
        # First boot already happened via fixture — flag should exist
        time.sleep(5)
        first_logs = container.logs().decode()
        assert "First boot" in first_logs, "First boot should have run"

        # Stop and restart container (same volume)
        container.stop()
        container.start()
        time.sleep(10)

        # Get only logs after restart
        logs = container.logs().decode()
        # Count occurrences of "First boot" — should only appear once (from first start)
        first_boot_count = logs.count("First boot: checking for GCS backup")
        assert first_boot_count == 1, (
            f"'First boot' appeared {first_boot_count} times, expected 1 (skip on restart)"
        )


# =========================================================================
# T2.9: First-boot with empty GCS (fresh project)
# =========================================================================


class TestT29FirstBootEmptyGCS:
    def test_fresh_start_with_empty_gcs(
        self, docker_client, sandbox_image, gcs_sa_key
    ):
        """Fresh volume + empty GCS prefix = fresh start, no restore."""
        import uuid

        unique_project = f"empty-{uuid.uuid4().hex[:8]}"
        vol_name = f"m2-test-empty-{uuid.uuid4().hex[:6]}"
        volume = docker_client.volumes.create(name=vol_name)
        name = f"m2-test-empty-{uuid.uuid4().hex[:6]}"

        container = docker_client.containers.run(
            "agent-sandbox:test",
            detach=True,
            name=name,
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            volumes={vol_name: {"bind": "/home/agent", "mode": "rw"}},
            environment={
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test",
                "GCS_SA_KEY": gcs_sa_key,
                "PROJECT_ID": unique_project,
                "GCS_BUCKET": GCS_BUCKET,
            },
        )
        try:
            time.sleep(15)
            logs = container.logs().decode()
            assert "No backup found" in logs, f"Expected 'No backup found' in logs"
            assert "fresh start" in logs.lower(), "Expected 'fresh start' message"

            exit_code, _ = container.exec_run(
                "test -f /home/agent/.sandbox_initialized"
            )
            assert exit_code == 0, ".sandbox_initialized flag not created"

            container.reload()
            assert container.status == "running", "Container should be running normally"
        finally:
            container.remove(force=True)
            volume.remove(force=True)


# =========================================================================
# T2.10: Backup daemon configurable interval
# =========================================================================


class TestT210BackupInterval:
    def test_custom_interval_10s(self, sandbox_container_fast_backup):
        """With BACKUP_INTERVAL_SECONDS=10, backup runs every ~10 seconds."""
        container = sandbox_container_fast_backup
        container.exec_run(
            "bash -c 'echo interval-test > /home/agent/interval.txt'",
            user="agent",
        )
        # Wait for at least 3 backup cycles at 10s interval
        time.sleep(40)
        logs = container.logs().decode()
        ok_count = logs.count("Backup OK")
        # Should have at least 2 "Backup OK" entries within 40s at 10s interval
        assert ok_count >= 2, (
            f"Expected >= 2 'Backup OK' in 40s at 10s interval, got {ok_count}"
        )

    def test_default_interval_is_300(self, docker_client, sandbox_image, gcs_sa_key):
        """Without BACKUP_INTERVAL_SECONDS, default should be 300."""
        import uuid

        name = f"m2-test-default-{uuid.uuid4().hex[:6]}"
        container = docker_client.containers.run(
            "agent-sandbox:test",
            detach=True,
            name=name,
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            environment={
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test",
                "GCS_SA_KEY": gcs_sa_key,
                "PROJECT_ID": f"test-{uuid.uuid4().hex[:8]}",
                "GCS_BUCKET": GCS_BUCKET,
            },
        )
        try:
            # The daemon runs one immediate backup on start, then sleeps INTERVAL.
            # At default 300s, after 20s we should see exactly 1 "Backup OK" (the initial one)
            # and NOT 2+ (which would mean the interval is too short).
            time.sleep(20)
            logs = container.logs().decode()
            ok_count = logs.count("Backup OK")
            assert ok_count <= 1, (
                f"Expected at most 1 'Backup OK' within 20s at default 300s interval, got {ok_count}"
            )
        finally:
            container.remove(force=True)


# =========================================================================
# T2.11: gcsfuse mount survives container restart
# =========================================================================


class TestT211MountSurvivesRestart:
    def test_gcsfuse_remount_after_restart(self, sandbox_container):
        """Stop + start container, verify gcsfuse re-mounts."""
        container = sandbox_container
        # Verify mount works before restart
        exit_code, _ = container.exec_run("ls /mnt/gcs")
        assert exit_code == 0, "Mount not accessible before restart"

        container.stop()
        container.start()
        time.sleep(10)

        container.reload()
        assert container.status == "running", "Container not running after restart"

        exit_code, output = container.exec_run("ls /mnt/gcs")
        assert exit_code == 0, f"/mnt/gcs not accessible after restart: {output.decode()}"
