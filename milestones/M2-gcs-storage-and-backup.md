# M2: GCS Storage & Backup System

| Field | Value |
|-------|-------|
| **Risk** | HIGH |
| **Dependencies** | M1 (base image), M3 (GCP SA for scoped access) |
| **Blocks** | M8 |
| **Plan sections** | 3.2, 5.1 (entrypoint), 6 (backup daemon), 9 (GCP setup) |

---

## Objective

Prove that GCS integration works end-to-end inside a sandbox container:
gcsfuse mounts, rclone backup daemon syncs files, and first-boot restore
recovers data from GCS. This validates the entire data persistence layer.

---

## Why This Is High Risk

- gcsfuse requires FUSE kernel module + SYS_ADMIN cap — known to be finicky in Docker
- gcsfuse performance is poor for random I/O — need to verify it's acceptable for read-only shared mount
- rclone sync can be slow for large file trees — need to validate backup daemon timing
- First-boot restore logic has edge cases (empty bucket, partial backup, corrupt data)
- GCP service account key injection and auth flow must work inside container

---

## Scope

**In scope:**
- GCS bucket creation with lifecycle rules
- Project Service SA setup (for creating per-project SAs)
- gcsfuse mount: project-scoped read-write at `/mnt/gcs`
- gcsfuse mount: shared read-only at `/mnt/shared`
- Backup daemon (rclone sync `/home/agent` → GCS every 5 min)
- First-boot restore (rclone sync GCS → `/home/agent`)
- GCS key injection via entrypoint

**Out of scope:**
- Per-project SA creation/deletion logic (M3)
- Conditional IAM bindings (M3)
- Container lifecycle orchestration (M4)

---

## Deliverables

```
# GCP infrastructure
- GCS bucket with 30-day lifecycle rule
- Project Service SA with storage.admin on bucket

# Code (already in sandbox image from M1, but now tested with real GCS)
backend/sandbox/entrypoint.sh         (gcsfuse mount, restore logic)
backend/sandbox/scripts/backup_daemon.py (rclone sync loop)

# Test infrastructure
tests/integration/test_gcs_storage.py
tests/integration/conftest.py         (GCS fixtures, container fixtures)
```

---

## Implementation Tasks

1. Create GCS bucket with 30-day lifecycle rule (steps 1-2 of GCP checklist)
2. Create Project Service SA with storage.admin on bucket (steps 3-5)
3. Generate a test SA key for integration testing
4. Run sandbox container with real GCS credentials
5. Verify gcsfuse mounts work (project-scoped + shared)
6. Verify backup daemon syncs files to GCS
7. Verify first-boot restore pulls files from GCS
8. Write all integration tests

---

## Test Cases

### T2.1: GCS bucket exists with correct lifecycle
**Type**: Infrastructure validation
**Command**:
```bash
gsutil ls gs://${GCS_BUCKET}
gsutil lifecycle get gs://${GCS_BUCKET}
```
**Assert**:
- Bucket exists
- Lifecycle rule: delete objects after 30 days

### T2.2: gcsfuse project mount (read-write)
**Type**: Integration (Docker + GCS)
**Setup**: Run container with valid GCS SA key, PROJECT_ID, GCS_BUCKET
**Steps**:
1. Start container with real GCS credentials
2. Wait for entrypoint to complete gcsfuse mount
3. Write a file inside container at `/mnt/gcs/test-file.txt`
4. Check GCS for the file
**Assert**:
- `docker exec sandbox ls /mnt/gcs` — mount is accessible
- `docker exec -u agent sandbox touch /mnt/gcs/test-file.txt` — write succeeds
- `gsutil ls gs://${GCS_BUCKET}/projects/${PROJECT_ID}/test-file.txt` — file exists in GCS
- `docker exec -u agent sandbox cat /mnt/gcs/test-file.txt` — readable back

### T2.3: gcsfuse shared mount (read-only)
**Type**: Integration (Docker + GCS)
**Setup**: Upload a file to `gs://${GCS_BUCKET}/shared/readme.txt` first
**Steps**:
1. Start container with real GCS credentials
2. Read the shared file from inside the container
3. Attempt to write — should fail
**Assert**:
- `docker exec -u agent sandbox cat /mnt/shared/readme.txt` — readable
- `docker exec -u agent sandbox touch /mnt/shared/new-file.txt` — fails (read-only)

### T2.4: Backup daemon syncs new files to GCS
**Type**: Integration (Docker + GCS)
**Setup**: Container running with real GCS credentials, backup interval set to 30s for testing
**Steps**:
1. Create a file: `docker exec -u agent sandbox bash -c "echo hello > /home/agent/test-backup.txt"`
2. Wait for backup daemon to run (check logs)
3. Check GCS for the file
**Assert**:
- File appears at `gs://${GCS_BUCKET}/projects/${PROJECT_ID}/workspace/test-backup.txt`
- Content matches: `hello`
- Backup daemon logs show "Backup OK"

### T2.5: Backup daemon syncs file deletions
**Type**: Integration (Docker + GCS)
**Setup**: File already backed up to GCS (from T2.4)
**Steps**:
1. Delete the file: `docker exec -u agent sandbox rm /home/agent/test-backup.txt`
2. Wait for next backup cycle
3. Check GCS
**Assert**:
- File no longer exists at `gs://${GCS_BUCKET}/projects/${PROJECT_ID}/workspace/test-backup.txt`
- (rclone sync deletes files at destination that don't exist at source)

### T2.6: Backup daemon handles errors gracefully
**Type**: Integration (Docker)
**Setup**: Container running with invalid GCS credentials
**Steps**:
1. Start container with bad GCS_SA_KEY
2. Wait for backup attempt
3. Check logs
**Assert**:
- Backup daemon logs "Backup failed" error
- Daemon does NOT crash — continues running and retries
- `supervisorctl status backup-daemon` shows RUNNING

### T2.7: First-boot restore from GCS
**Type**: Integration (Docker + GCS)
**Setup**:
1. Upload test files to `gs://${GCS_BUCKET}/projects/${PROJECT_ID}/workspace/`
2. Create a fresh volume (no `.sandbox_initialized` flag)
**Steps**:
1. Start container with fresh volume + valid GCS credentials
2. Wait for entrypoint to complete
3. Check `/home/agent` for restored files
**Assert**:
- Container logs show "First boot: checking for GCS backup..."
- Container logs show "Backup found — restoring..."
- Files from GCS are present in `/home/agent/`
- `.sandbox_initialized` flag exists

### T2.8: First-boot skipped when flag exists
**Type**: Integration (Docker + GCS)
**Setup**: Volume already has `.sandbox_initialized` (from previous run)
**Steps**:
1. Start container with existing volume
2. Check container logs
**Assert**:
- Logs do NOT contain "First boot: checking for GCS backup"
- Entrypoint proceeds directly to supervisord

### T2.9: First-boot with empty GCS (fresh project)
**Type**: Integration (Docker + GCS)
**Setup**: Empty GCS prefix (no backup exists)
**Steps**:
1. Start container with fresh volume + valid GCS credentials
2. Wait for entrypoint
**Assert**:
- Container logs show "No backup found — fresh start."
- `.sandbox_initialized` flag created
- Container starts normally

### T2.10: Backup daemon configurable interval
**Type**: Integration (Docker)
**Setup**: Set `BACKUP_INTERVAL_SECONDS=10`
**Assert**:
- Backup runs every ~10 seconds (verify via log timestamps)
- Default (no env var) is 300 seconds

### T2.11: gcsfuse mount survives container restart
**Type**: Integration (Docker + GCS)
**Steps**:
1. Start container, verify gcsfuse mount
2. `docker stop` + `docker start`
3. Check mount
**Assert**:
- After restart, entrypoint re-mounts gcsfuse
- `/mnt/gcs` is accessible again

---

## Acceptance Criteria

- [ ] GCS bucket exists with lifecycle rule
- [ ] Project Service SA created with correct permissions
- [ ] All 11 test cases pass
- [ ] Backup daemon runs stable for 30+ minutes without crashes
- [ ] gcsfuse performance acceptable for read-only shared mount (ls, cat of small files < 2s)
