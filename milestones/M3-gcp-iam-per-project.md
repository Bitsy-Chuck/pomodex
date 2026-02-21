# M3: GCP IAM — Per-Project Service Accounts

| Field | Value |
|-------|-------|
| **Risk** | HIGH |
| **Dependencies** | None (M2 for full GCS integration testing) |
| **Blocks** | M8 |
| **Plan sections** | 3.2, 5.1, 5.5, 9 |

---

## Objective

Implement and validate the GCP IAM service account lifecycle: create a
per-project service account, set conditional IAM bindings that scope it to
only that project's GCS prefix, generate keys, and clean up on deletion.
This is the tenant isolation layer for GCS.

---

## Why This Is High Risk

- GCP IAM conditional bindings are complex — `resource.name.startsWith()` conditions on storage
- IAM propagation delay — bindings may take seconds to minutes to take effect
- Service account key management — creating, injecting, and cleaning up JSON keys
- Quota limits — GCP limits SAs per project (~100 by default)
- API errors are opaque — permission denied messages don't always say what's missing

---

## Scope

**In scope:**
- Python module for GCP IAM operations (create SA, delete SA, create key, set IAM binding)
- Conditional IAM binding: SA can only access `gs://bucket/projects/{project_id}/*`
- Conditional IAM binding: SA can read `gs://bucket/shared/*`
- SA key generation and retrieval
- SA deletion and IAM cleanup
- Integration tests against real GCP

**Out of scope:**
- Container creation/injection (M4)
- Full Project Service API (M8)

---

## Deliverables

```
backend/project-service/
  services/gcp_iam.py          # SA CRUD, IAM binding management
  tests/integration/test_gcp_iam.py
```

---

## Implementation Tasks

1. Set up GCP project with IAM API enabled
2. Create Project Service SA with `roles/iam.serviceAccountAdmin` + `roles/storage.admin`
3. Implement `create_service_account(project_id)` — creates SA with naming convention
4. Implement `create_sa_key(sa_email)` — generates JSON key
5. Implement `grant_gcs_iam(sa_email, bucket, prefix)` — sets conditional IAM bindings
6. Implement `delete_service_account(sa_email)` — deletes SA + all keys
7. Write integration tests against real GCP

---

## Test Cases

### T3.1: Create service account with correct naming
**Type**: Integration (GCP API)
**Steps**:
1. Call `create_service_account("test-project-123")`
2. Verify SA exists in GCP
**Assert**:
- SA email follows pattern: `sa-{short_id}@{gcp_project}.iam.gserviceaccount.com`
- SA display name includes project ID for identification
- SA is listable via `gcloud iam service-accounts list`

### T3.2: Generate SA key
**Type**: Integration (GCP API)
**Steps**:
1. Create SA (from T3.1)
2. Call `create_sa_key(sa_email)`
**Assert**:
- Returns a valid JSON key string
- Key contains `type`, `project_id`, `private_key_id`, `private_key`, `client_email`
- Key's `client_email` matches the SA email

### T3.3: IAM binding — SA can write to its own prefix
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Create SA + key
2. Set IAM binding for `gs://bucket/projects/{project_id}/*` with `roles/storage.objectAdmin`
3. Using the SA's key, upload a file to `gs://bucket/projects/{project_id}/test.txt`
**Assert**:
- Upload succeeds
- File is readable using the SA's credentials

### T3.4: IAM binding — SA cannot write to another project's prefix
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Create SA for project A
2. Set IAM binding scoped to project A's prefix
3. Using project A's SA, try to write to `gs://bucket/projects/{OTHER_PROJECT_ID}/test.txt`
**Assert**:
- Write fails with 403 Forbidden
- Error message indicates insufficient permissions

### T3.5: IAM binding — SA can read shared prefix
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Create SA + key
2. Set IAM binding for `gs://bucket/shared/*` with `roles/storage.objectViewer`
3. Upload a file to shared prefix (using admin credentials)
4. Using the SA's key, read the file from `gs://bucket/shared/test.txt`
**Assert**:
- Read succeeds

### T3.6: IAM binding — SA cannot write to shared prefix
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Using the per-project SA's key, try to write to `gs://bucket/shared/test.txt`
**Assert**:
- Write fails with 403 Forbidden

### T3.7: IAM binding — SA cannot read other project's prefix
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Create SA for project A
2. Upload a file to project B's prefix (using admin credentials)
3. Using project A's SA, try to read from project B's prefix
**Assert**:
- Read fails with 403 Forbidden

### T3.8: Delete service account
**Type**: Integration (GCP API)
**Steps**:
1. Create SA
2. Call `delete_service_account(sa_email)`
3. Verify SA no longer exists
**Assert**:
- SA is not listable in `gcloud iam service-accounts list`
- All keys for the SA are deleted
- IAM bindings referencing the SA are cleaned up

### T3.9: Delete non-existent SA is idempotent
**Type**: Integration (GCP API)
**Steps**:
1. Call `delete_service_account("nonexistent@project.iam.gserviceaccount.com")`
**Assert**:
- No exception thrown (or handled gracefully)
- Returns success

### T3.10: IAM propagation timing
**Type**: Integration (GCP API + GCS)
**Steps**:
1. Create SA + set IAM binding
2. Immediately try to use the SA to access GCS
3. Retry with backoff if it fails
**Assert**:
- Access works within 60 seconds of binding creation
- Document the observed propagation delay

### T3.11: SA naming handles UUID truncation
**Type**: Unit test
**Steps**:
1. Pass various project IDs (UUID format) to naming function
2. Verify SA ID is <= 30 chars (GCP limit) and valid
**Assert**:
- SA ID uses only lowercase letters, digits, hyphens
- SA ID is between 6-30 characters
- SA ID is deterministic given the same project ID

---

## Acceptance Criteria

- [ ] All 11 test cases pass
- [ ] SA CRUD operations are idempotent (can be retried safely)
- [ ] IAM bindings enforce strict project isolation
- [ ] SA deletion cleans up all associated resources
- [ ] Observed IAM propagation delay documented
