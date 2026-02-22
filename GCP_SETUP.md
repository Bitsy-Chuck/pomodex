# GCP Setup Commands Reference

All commands used to set up the GCP account for this project.

## 1. Create GCS Bucket

```bash
gsutil mb -l europe-west1 gs://pomodex-fd2bcd-sandbox
```

## 2. Set 30-day Lifecycle Rule

```bash
cat > lifecycle.json << 'EOF'
{ "rule": [{ "action": {"type": "Delete"},
             "condition": {"age": 30} }] }
EOF
gsutil lifecycle set lifecycle.json gs://pomodex-fd2bcd-sandbox
```

## 3. Enable Uniform Bucket-Level Access

Required for conditional IAM bindings (per-project prefix scoping).

```bash
gsutil ubla set on gs://pomodex-fd2bcd-sandbox
```

## 4. Create Project Service SA

```bash
gcloud iam service-accounts create pomodex-project-service \
    --display-name="Pomodex - Project Service"
```

## 5. Grant IAM Roles to Project Service SA

### Service Account Admin (create/delete SAs)

```bash
gcloud projects add-iam-policy-binding pomodex-fd2bcd \
    --member="serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountAdmin"
```

### Service Account Key Admin (create/delete SA keys)

```bash
gcloud projects add-iam-policy-binding pomodex-fd2bcd \
    --member="serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountKeyAdmin"
```

### Storage Admin on Bucket (set per-project IAM conditions)

```bash
gsutil iam ch \
    serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com:roles/storage.admin \
    gs://pomodex-fd2bcd-sandbox
```

## 6. Download Project Service SA Key

```bash
gcloud iam service-accounts keys create ./secrets/gcs-test-key.json \
    --iam-account=pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com
```

## 7. Enable Required APIs

```bash
gcloud services enable \
    iam.googleapis.com \
    storage.googleapis.com \
    artifactregistry.googleapis.com
```

## 8. (Future) Artifact Registry Setup

```bash
gcloud artifacts repositories create sandboxes \
    --repository-format=docker \
    --location=europe-west1 \
    --description="Sandbox container snapshots"

gcloud artifacts repositories add-iam-policy-binding sandboxes \
    --location=europe-west1 \
    --member="serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"
```

## 9. (Future) GCP Firewall Rules

```bash
gcloud compute firewall-rules create allow-platform \
    --allow=tcp:22,tcp:8000,tcp:9000,tcp:10000-11000 \
    --target-tags=sandbox-vm \
    --description="SSH + Project Service + Terminal Proxy + sandbox SSH ports"
```

## Summary of SA Roles

| Role | Purpose |
|------|---------|
| `roles/iam.serviceAccountAdmin` | Create/delete per-project service accounts |
| `roles/iam.serviceAccountKeyAdmin` | Create/delete SA JSON keys |
| `roles/storage.admin` (on bucket) | Set conditional IAM bindings on the bucket |

## Key Files

| File | Purpose |
|------|---------|
| `secrets/gcs-test-key.json` | Project Service SA key (for running IAM operations) |
| `.env` | Environment variables (GCP_PROJECT, GCS_BUCKET, etc.) |

## Docker Daemon Configuration

Configure Docker to allocate /24 subnets for bridge networks (default is /16, which exhausts address space after ~15 networks).

**Docker Desktop (dev):** Settings > Docker Engine, add to JSON:

**Linux (production):** Edit `/etc/docker/daemon.json`:

```json
{
  "default-address-pools": [
    {"base": "172.16.0.0/12", "size": 24}
  ]
}
```

Restart Docker after applying. Supports up to 4,096 concurrent bridge networks.
