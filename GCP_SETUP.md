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

## 8. Artifact Registry Setup (M5)

### Create repository

```bash
gcloud artifacts repositories create sandboxes \
    --repository-format=docker \
    --location=europe-west1 \
    --project=pomodex-fd2bcd \
    --description="Sandbox container snapshots"
```

### Grant writer + reader roles to Project Service SA

```bash
gcloud artifacts repositories add-iam-policy-binding sandboxes \
    --location=europe-west1 \
    --project=pomodex-fd2bcd \
    --member="serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"

gcloud artifacts repositories add-iam-policy-binding sandboxes \
    --location=europe-west1 \
    --project=pomodex-fd2bcd \
    --member="serviceAccount:pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader"
```

### Configure cleanup policy — keep last 5 versions per image

```bash
cat > /tmp/ar-cleanup-policy.json << 'EOF'
[
  {
    "name": "keep-last-5",
    "action": {"type": "Keep"},
    "mostRecentVersions": {"keepCount": 5}
  }
]
EOF
gcloud artifacts repositories set-cleanup-policies sandboxes \
    --location=europe-west1 \
    --project=pomodex-fd2bcd \
    --policy=/tmp/ar-cleanup-policy.json
```

## 9. Auto-Created SA via Docker Compose

`docker compose up` runs an init service that auto-creates a second SA (`project-service-sa`) using the host's gcloud credentials. See `scripts/init-gcp-sa.sh`. It grants:

| Role | Purpose |
|------|---------|
| `roles/iam.serviceAccountAdmin` | Create/delete per-project SAs at runtime |
| `roles/iam.serviceAccountKeyAdmin` | Create/delete per-project SA JSON keys |
| `roles/storage.admin` | GCS bucket access for project storage |
| `roles/artifactregistry.writer` (on sandboxes repo) | Push snapshot images |

The SA key is stored in the `secrets-data` Docker volume at `/secrets/project-service-sa.json`. A JWT signing secret is also generated at `/secrets/jwt-secret`.

To force regeneration:
```bash
docker compose down
docker volume rm pomodex_secrets-data
docker compose up
```

## 10. Enable Compute Engine API

```bash
# Required before creating VMs
gcloud services enable compute.googleapis.com --project=pomodex-fd2bcd
```

## 11. Artifact Registry — Platform Images Repo

Separate from the `sandboxes` repo (step 8), this stores the platform service images.

```bash
# Create a Docker repo for platform service images (project-service, terminal-proxy, agent-sandbox)
gcloud artifacts repositories create pomodex \
    --repository-format=docker \
    --location=europe-west1 \
    --project=pomodex-fd2bcd

# Authenticate Docker to push/pull from Artifact Registry
gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet
```

## 12. Build & Push Platform Images

```bash
# Build all 3 service images for linux/amd64 and push to Artifact Registry
# Images: project-service, terminal-proxy, agent-sandbox
./scripts/push-images.sh
```

Pushes to:
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/project-service:latest`
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/terminal-proxy:latest`
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/agent-sandbox:latest`

## 13. Deploy VM

```bash
# Create a VM, upload files, install Docker, and start all services
./scripts/deploy-vm.sh <vm-name> [zone]
# Example:
./scripts/deploy-vm.sh pomodex-prod europe-west1-b
```

The deploy script runs these commands under the hood:

```bash
# Create an Ubuntu VM with Docker-appropriate specs
# --scopes=cloud-platform gives the VM access to GCP APIs (AR, GCS, IAM)
# --tags=http-server allows firewall rules to target this VM
gcloud compute instances create pomodex-prod \
    --project=pomodex-fd2bcd \
    --zone=europe-west1-b \
    --machine-type=e2-medium \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=30GB \
    --tags=http-server \
    --scopes=cloud-platform

# SSH into the VM and install Docker + Docker Compose plugin
gcloud compute ssh pomodex-prod --zone=europe-west1-b --command="
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-plugin
    sudo systemctl enable docker
    sudo usermod -aG docker \$USER
"

# Configure Docker on the VM to authenticate with Artifact Registry
gcloud compute ssh pomodex-prod --zone=europe-west1-b --command="
    gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet
"

# Upload the 3 required files to the VM (no full repo needed)
gcloud compute scp docker-compose.prod.yml pomodex-prod:~/pomodex/docker-compose.yml --zone=europe-west1-b
gcloud compute scp scripts/init-gcp-sa.sh pomodex-prod:~/pomodex/scripts/init-gcp-sa.sh --zone=europe-west1-b
gcloud compute scp .env.prod pomodex-prod:~/pomodex/.env --zone=europe-west1-b

# Pull the sandbox image and tag it locally
# (project-service spawns sandbox containers by local image tag)
gcloud compute ssh pomodex-prod --zone=europe-west1-b --command="
    docker pull europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/agent-sandbox:latest
    docker tag europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/agent-sandbox:latest agent-sandbox:latest
"

# Start all services
gcloud compute ssh pomodex-prod --zone=europe-west1-b --command="
    cd ~/pomodex && docker compose up -d
"
```

## 14. Firewall Rules

```bash
# Allow external traffic to the project-service API on port 8000
gcloud compute firewall-rules create allow-pomodex \
    --project=pomodex-fd2bcd \
    --allow=tcp:8000 \
    --target-tags=http-server \
    --source-ranges=0.0.0.0/0
```

## 15. Common VM Operations

```bash
# SSH into the VM
gcloud compute ssh pomodex-prod --zone=europe-west1-b

# View service logs
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- 'cd ~/pomodex && docker compose logs -f'

# Update running VM with new images
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose pull && docker compose up -d'

# Get VM external IP
gcloud compute instances describe pomodex-prod \
    --zone=europe-west1-b \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)"

# Delete VM (destroys all data including postgres volume)
gcloud compute instances delete pomodex-prod --zone=europe-west1-b
```

## Summary of Service Accounts

### `pomodex-project-service` (manual, see steps 4-6)

Created manually for tests and direct gcloud usage.

| Role | Purpose |
|------|---------|
| `roles/iam.serviceAccountAdmin` | Create/delete per-project service accounts |
| `roles/iam.serviceAccountKeyAdmin` | Create/delete SA JSON keys |
| `roles/storage.admin` (on bucket) | Set conditional IAM bindings on the bucket |
| `roles/artifactregistry.writer` (on sandboxes repo) | Push snapshot images |
| `roles/artifactregistry.reader` (on sandboxes repo) | Pull snapshot images for restore |

### `project-service-sa` (auto-created, see step 9)

Created automatically by `docker compose up` via `scripts/init-gcp-sa.sh`.

| Role | Purpose |
|------|---------|
| `roles/iam.serviceAccountAdmin` | Create/delete per-project service accounts |
| `roles/iam.serviceAccountKeyAdmin` | Create/delete per-project SA JSON keys |
| `roles/storage.admin` | GCS bucket access |
| `roles/artifactregistry.writer` (on sandboxes repo) | Push snapshot images |

## Key Files

| File | Purpose |
|------|---------|
| `secrets/gcs-test-key.json` | Manual SA key (for tests) |
| `secrets-data` volume: `project-service-sa.json` | Auto-created SA key (used by docker-compose) |
| `secrets-data` volume: `jwt-secret` | Auto-generated JWT signing key |
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
