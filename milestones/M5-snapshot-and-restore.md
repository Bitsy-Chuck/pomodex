# M5: Snapshot & Restore (Artifact Registry)

| Field | Value |
|-------|-------|
| **Risk** | HIGH |
| **Dependencies** | M4 (container lifecycle) |
| **Blocks** | M8 |
| **Plan sections** | 5.2, 5.3, 5.4, 9 (steps 7-9) |

---

## Objective

Implement the snapshot pipeline: `docker commit` a running container into an
image, push to Artifact Registry with timestamp + latest tags, and restore
by pulling the snapshot image. Validate both the fast path (snapshot image +
existing volume) and the fallback path (fresh base image + GCS restore).

---

## Why This Is High Risk

- `docker commit` captures filesystem state but NOT volume contents — must be understood
- Artifact Registry push/pull timing for large images (could be 1GB+)
- Tag management — timestamp + latest, cleanup of old tags
- Restore flow has two paths (fast/fallback) that both need to work
- Interaction between committed image layers and volume mount on restore
- rclone final sync before commit must complete fully (data consistency)

---

## Scope

**In scope:**
- Artifact Registry repository creation and configuration
- `docker commit` to create snapshot image
- Tag with timestamp + `:latest`
- Push to Artifact Registry
- Pull snapshot image on restore
- Container recreation from snapshot image
- Cleanup policy (keep last 5 tags per project)
- Final rclone sync before snapshot

**Out of scope:**
- Inactivity detection (M8 — background task in Project Service)
- API endpoints for snapshot/restore (M8)
- DB persistence (M8 — functions return metadata dicts, M8 wires persistence)

**Implementation decisions:**
- AR Docker auth: Programmatic `docker login` using SA key JSON (`_json_key` username). No manual `gcloud auth configure-docker` required.
- DB fields: Functions return metadata dicts. M8 persists them.
- T5.11 (cleanup policy): Removed from tests. Infra-only, configured via `gcloud`, verified manually.

---

## Deliverables

```
backend/project_service/
  services/snapshot_manager.py    # commit, push, pull, restore logic
tests/
  integration/test_snapshot.py
  unit/test_snapshot_logic.py     # T5.9 image selection unit test

# GCP
- Artifact Registry repository "sandboxes" with cleanup policy
```

---

## Implementation Tasks

1. Create Artifact Registry repository `sandboxes` (GCP step 7)
2. Grant Project Service SA `artifactregistry.writer` role (GCP step 8)
3. Configure cleanup policy — keep last 5 tags per project (GCP step 9)
   - **Note:** Policy is infra-only (async on Google's schedule). No test — verified manually once.
4. Implement `snapshot_project(project_id)`:
   - Run final rclone sync inside container
   - `docker commit` → tag with timestamp + latest
   - Authenticate Docker to AR programmatically using SA key JSON (`docker login` with `_json_key` username) — no manual `gcloud auth configure-docker` needed
   - Push both tags to Artifact Registry
   - Return metadata dict (`snapshot_image`, `last_snapshot_at`, `status`) — DB persistence deferred to M8
5. Implement `restore_from_snapshot(project_id)`:
   - Pull snapshot image from Artifact Registry
   - Create new container from snapshot image
   - Attach existing volume
6. Implement `restore_from_gcs(project_id)`:
   - Use base image
   - Create new container (entrypoint handles GCS restore)
7. Implement `delete_snapshot_images(project_id)`:
   - Delete all tags for a project in Artifact Registry
8. Write all tests

---

## Test Cases

### T5.1: Docker commit creates image from running container
**Type**: Integration (Docker)
**Steps**:
1. Start a container, install a package inside (`apt-get install -y cowsay`)
2. Run `docker commit` on the container
3. Start a new container from the committed image
**Assert**:
- New container has `cowsay` installed (system state preserved)
- Image is tagged correctly

### T5.2: Committed image does NOT contain volume data
**Type**: Integration (Docker)
**Steps**:
1. Start container with volume at `/home/agent`
2. Write a file to `/home/agent/volume-only.txt`
3. `docker commit` the container
4. Start new container from committed image WITHOUT the original volume
5. Check `/home/agent/volume-only.txt`
**Assert**:
- File does NOT exist in the new container
- This confirms volume data is separate from image layers

### T5.3: Push to Artifact Registry with correct tags
**Type**: Integration (Docker + GCP)
**Steps**:
1. Commit a container image
2. Tag with `{registry}/{project_id}:{timestamp}` and `{registry}/{project_id}:latest`
3. Push both tags
**Assert**:
- Both tags visible in Artifact Registry (`gcloud artifacts docker images list`)
- Image digest is the same for both tags

### T5.4: Pull snapshot image from Artifact Registry
**Type**: Integration (Docker + GCP)
**Steps**:
1. (From T5.3) Image is in Artifact Registry
2. Remove local copy: `docker rmi {image}`
3. Pull using the latest tag
**Assert**:
- Pull succeeds
- Image is available locally
- Image matches the pushed digest

### T5.5: Fast restore — snapshot image + existing volume
**Type**: Integration (Docker + GCP)
**Steps**:
1. Create container, install a package, write file to `/home/agent/data.txt`
2. Snapshot (commit + push)
3. Stop + remove container (keep volume)
4. Restore: pull snapshot image, create container, attach existing volume
**Assert**:
- New container has the installed package (from image)
- `/home/agent/data.txt` exists (from volume)
- Container is fully functional (sshd, ttyd running)

### T5.6: Fallback restore — base image + GCS restore
**Type**: Integration (Docker + GCS)
**Steps**:
1. Container has files in `/home/agent/`, backed up to GCS
2. Delete both container AND volume (simulating disaster)
3. Restore using base image + fresh volume
4. Container starts, entrypoint triggers GCS restore
**Assert**:
- Container logs show "First boot: checking for GCS backup..."
- Container logs show "Backup found — restoring..."
- Files from GCS are restored to `/home/agent/`
- Container is functional after restore

### T5.7: Final rclone sync runs before commit
**Type**: Integration (Docker + GCS)
**Steps**:
1. Create a file in `/home/agent/last-minute.txt`
2. Trigger snapshot (which runs rclone sync first)
3. Check GCS after snapshot
**Assert**:
- `last-minute.txt` exists in GCS (was synced before commit)
- `last_backup_at` timestamp is updated

### T5.8: Snapshot returns correct metadata
**Type**: Integration (Docker)
**Steps**:
1. Trigger snapshot for a project
2. Inspect returned metadata dict
**Assert**:
- `snapshot_image` is set to `{registry}/{project_id}:latest`
- `last_snapshot_at` is set to current time (within tolerance)
- `status` is `"stopped"`
- **Note:** DB persistence deferred to M8. This test verifies the function returns
  correct metadata that M8's API layer will persist.

### T5.9: Restore determines correct image
**Type**: Unit test
**Steps**:
1. Call with `snapshot_image` set → should return snapshot image
2. Call with `snapshot_image` = None → should return base image
**Assert**:
- Logic correctly selects image source based on `snapshot_image` parameter
- **Note:** No DB dependency. Pure function that takes `snapshot_image` arg and returns the image to use.

### T5.10: Delete snapshot images from Artifact Registry
**Type**: Integration (Docker + GCP)
**Steps**:
1. Push multiple snapshot tags for a project
2. Call `delete_snapshot_images(project_id)`
3. List images in Artifact Registry
**Assert**:
- No images remain for that project ID
- Other projects' images are unaffected

### ~~T5.11: Cleanup policy keeps only 5 latest tags~~ — REMOVED
**Reason**: AR cleanup policies run asynchronously on Google's schedule. There is
no API to trigger or wait for them, making this untestable in CI/integration.
The cleanup policy is configured via `gcloud` during GCP setup (documented in
`GCP_SETUP.md`) and verified manually once.

### T5.12: Snapshot of large container (performance)
**Type**: Performance test
**Steps**:
1. Create container with ~500MB of installed packages
2. Measure time for: commit + push
**Assert**:
- Document the timing (commit time, push time, total)
- Total should be < 1 minute for reasonable image sizes

### T5.13: Container stop after snapshot
**Type**: Integration (Docker)
**Steps**:
1. Trigger snapshot
2. After push completes, check container state
**Assert**:
- Container is stopped and removed
- Volume still exists
- Returned metadata has `status` = `"stopped"`

---

## Acceptance Criteria

- [ ] Artifact Registry repository created with cleanup policy
- [ ] All 12 test cases pass (T5.11 removed — AR cleanup policy is infra-only)
- [ ] Fast restore creates a fully functional container from snapshot in < 60 seconds
- [ ] Fallback restore works when volume is lost
- [ ] No data loss — final rclone sync always completes before commit
