# Pomodex

Self-hosted cloud sandbox platform. Spin up isolated Ubuntu containers on demand, each with its own SSH access and browser-based terminal. Workspaces persist to GCS automatically. Built for giving AI agents or developers their own disposable dev environments.

## What is this?

Pomodex lets you create isolated sandbox environments from a web UI. Each sandbox is a full Ubuntu 24.04 container with:

- **Browser terminal** — xterm.js in the browser, connected via WebSocket to ttyd inside the container
- **SSH access** — auto-generated ED25519 keypair per sandbox, connect from any SSH client
- **Persistent workspace** — `/home/agent` syncs to Google Cloud Storage every 5 minutes via rclone
- **Snapshot/restore** — save full container state as a Docker image to Artifact Registry, restore later
- **Pre-installed tools** — Python, Node.js, git, tmux, Claude Code CLI, gcsfuse

## Why?

- **Isolation** — each sandbox runs in its own Docker container with its own network. Sandboxes can't see or interfere with each other.
- **No port conflicts** — SSH ports are dynamically allocated. Terminal access goes through a WebSocket proxy chain, so only port 80 is exposed externally.
- **Cheap to run** — a single `e2-medium` GCP VM ($25/mo) can host dozens of concurrent sandboxes. No Kubernetes, no managed container service overhead.
- **Simple to deploy** — one script (`deploy-vm.sh`) creates a VM, installs Docker, uploads configs, and starts everything. No Terraform, no Ansible.
- **Automatic backups** — workspace files sync to GCS continuously. If a container dies, the next one restores from the last backup on first boot.

## How Networking Works

Only **port 80** is exposed to the internet. Everything else stays internal.

```
Internet
   │
   ▼ port 80
┌─────────────────────────────────────────────────────────┐
│  nginx (sandbox-web)                                    │
│    /auth, /projects  ──► project-service:8000            │
│    /ws/terminal/*    ──► project-service:8000             │
│                            │                             │
│                            ▼ internal WebSocket proxy    │
│                        terminal-proxy:9000               │
│                            │                             │
│                            ▼ per-sandbox Docker network  │
│                        ttyd:7681 (inside sandbox)        │
│                                                         │
│  postgres:5432 (internal only)                          │
│  sandbox SSH:22 ──► dynamic host port (30000+)          │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Double WebSocket proxy** — browser connects to nginx, which proxies to project-service, which proxies to terminal-proxy, which connects to ttyd inside the sandbox. This keeps terminal-proxy and sandbox ports completely off the public internet.
- **Per-sandbox networks** — each sandbox gets its own Docker bridge network (`net-{project_id}`). Docker daemon is configured with /24 subnets to support up to ~4,096 concurrent networks.
- **JWT auth on terminal** — terminal WebSocket connections carry a JWT token as a query param. The terminal-proxy validates it against project-service before proxying to ttyd.
- **No direct container access** — sandboxes are never port-mapped to the host (except SSH). All HTTP/WebSocket traffic routes through nginx.

## Use Cases

- **AI agent sandboxes** — give each agent its own isolated environment with SSH + terminal access
- **Disposable dev environments** — spin up a clean Ubuntu box, do your work, snapshot it, tear it down
- **Teaching/workshops** — provision environments for students with pre-installed tools
- **CI-adjacent tasks** — run untrusted code in isolated containers with automatic cleanup

## Roadmap

Features under consideration (vote on the [landing page](https://pomodex.dev)):

- **Browser Streaming (VNC)** — stream a full desktop via KasmVNC, run GUI apps and IDEs in the browser
- **Custom Base Images** — start sandboxes from your own Docker images with pre-installed stacks
- **Team Sharing** — share sandbox access with teammates, collaborative terminal sessions
- **API & SDK** — Python and TypeScript SDKs for programmatic sandbox management
- **GPU Instances** — attach GPUs for ML training, inference, and accelerated workloads
- **Multi-Cloud Support** — deploy on AWS or Azure in addition to GCP

---

## Architecture

```
Browser ──► nginx (port 80)
              ├── /auth, /projects ──► project-service (port 8000)
              └── /ws/terminal/*   ──► project-service ──► terminal-proxy (port 9000) ──► ttyd (port 7681 inside sandbox)

project-service ──► Docker API (creates/manages sandbox containers)
                ──► PostgreSQL (user accounts, project metadata)
                ──► GCS (workspace backups)
                ──► Artifact Registry (sandbox snapshots)
```

**Services:**

| Service | Description |
|---------|-------------|
| `sandbox-web` | React SPA + nginx reverse proxy |
| `project-service` | FastAPI backend — auth, project CRUD, sandbox lifecycle |
| `terminal-proxy` | WebSocket proxy — routes terminal connections to sandbox ttyd |
| `postgres` | PostgreSQL 16 — stores users, projects, tokens |
| `agent-sandbox` | Ubuntu 24.04 container — SSH, ttyd, gcsfuse, rclone backup |

## Prerequisites

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud` CLI)
- [Docker](https://docs.docker.com/get-docker/) (Docker Desktop for Mac/Windows, or Docker Engine for Linux)
- A GCP account with billing enabled

## Quick Start (Local Development)

If GCP is already set up (steps 1-3 below are done), just run:

```bash
git clone <repo-url> && cd pomodex
cp .env.example .env  # fill in your values
./scripts/setup-local.sh
```

Open http://localhost.

---

## Full Setup from Scratch

### Step 1: Create GCP Project

```bash
# Create project (or use existing)
gcloud projects create pomodex-fd2bcd
gcloud config set project pomodex-fd2bcd

# Enable billing (required — do this in the console)
# https://console.cloud.google.com/billing

# Enable required APIs
gcloud services enable \
    iam.googleapis.com \
    storage.googleapis.com \
    artifactregistry.googleapis.com \
    compute.googleapis.com
```

### Step 2: Create GCS Bucket

Used for sandbox workspace backups (rclone syncs `/home/agent` every 5 minutes).

```bash
# Create bucket
gsutil mb -l europe-west1 gs://pomodex-fd2bcd-sandbox

# Set 30-day auto-delete lifecycle
cat > /tmp/lifecycle.json << 'EOF'
{ "rule": [{ "action": {"type": "Delete"},
             "condition": {"age": 30} }] }
EOF
gsutil lifecycle set /tmp/lifecycle.json gs://pomodex-fd2bcd-sandbox

# Enable uniform bucket-level access (required for IAM conditions)
gsutil ubla set on gs://pomodex-fd2bcd-sandbox
```

### Step 3: Create Artifact Registry Repos

Two repos: `sandboxes` for snapshot images, `pomodex` for platform service images.

```bash
# Snapshot images repo
gcloud artifacts repositories create sandboxes \
    --repository-format=docker \
    --location=europe-west1 \
    --description="Sandbox container snapshots"

# Configure cleanup — keep last 5 versions per image
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
    --policy=/tmp/ar-cleanup-policy.json

# Platform service images repo
gcloud artifacts repositories create pomodex \
    --repository-format=docker \
    --location=europe-west1

# Authenticate Docker to push/pull
gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet
```

### Step 4: Create Service Account (manual, for local dev)

```bash
gcloud iam service-accounts create pomodex-project-service \
    --display-name="Pomodex - Project Service"

SA_EMAIL="pomodex-project-service@pomodex-fd2bcd.iam.gserviceaccount.com"

# Grant project-level IAM roles
for role in roles/iam.serviceAccountAdmin roles/iam.serviceAccountKeyAdmin; do
    gcloud projects add-iam-policy-binding pomodex-fd2bcd \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --quiet
done

# Grant storage admin on bucket
gsutil iam ch "serviceAccount:${SA_EMAIL}:roles/storage.admin" gs://pomodex-fd2bcd-sandbox

# Grant Artifact Registry access on sandboxes repo
for role in roles/artifactregistry.writer roles/artifactregistry.reader; do
    gcloud artifacts repositories add-iam-policy-binding sandboxes \
        --location=europe-west1 \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --quiet
done

# Download key
mkdir -p secrets
gcloud iam service-accounts keys create ./secrets/gcs-test-key.json \
    --iam-account="$SA_EMAIL"
```

### Step 5: Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```
GCP_PROJECT=pomodex-fd2bcd
GCS_BUCKET=pomodex-fd2bcd-sandbox
```

### Step 6: Configure Docker Daemon

Each sandbox gets its own Docker bridge network. Default /16 subnets exhaust address space after ~15 networks. Fix by using /24 subnets:

**Docker Desktop (Mac/Windows):** Settings > Docker Engine, add:

```json
{
  "default-address-pools": [
    {"base": "172.16.0.0/12", "size": 24}
  ]
}
```

**Linux:** Edit `/etc/docker/daemon.json` with the same content, then `sudo systemctl restart docker`.

This supports up to ~4,096 concurrent sandbox networks.

### Step 7: Run Locally

```bash
./scripts/setup-local.sh
```

This builds the `agent-sandbox` image and starts all services via `docker compose up --build`.

Open http://localhost.

---

## Production Deployment (GCP VM)

### Step 1: Build & Push Images

Builds all 4 images for `linux/amd64` and pushes to Artifact Registry.

```bash
./scripts/push-images.sh
```

Images pushed:
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/project-service:latest`
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/terminal-proxy:latest`
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/agent-sandbox:latest`
- `europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/sandbox-web:latest`

### Step 2: Create `.env.prod`

```bash
cat > .env.prod << 'EOF'
GCP_PROJECT=pomodex-fd2bcd
GCS_BUCKET=pomodex-fd2bcd-sandbox
POSTGRES_PASSWORD=<generate-a-strong-password>
CORS_ORIGINS=*
HOST_IP=<will-be-set-after-vm-creation>
EOF
```

### Step 3: Create Firewall Rule

```bash
# Allow HTTP traffic to VMs tagged http-server
gcloud compute firewall-rules create allow-pomodex \
    --allow=tcp:80 \
    --target-tags=http-server \
    --source-ranges=0.0.0.0/0
```

### Step 4: Deploy VM

```bash
./scripts/deploy-vm.sh pomodex-prod europe-west1-b
```

This script:
1. Creates an Ubuntu 24.04 VM (`e2-medium`, 30GB disk)
2. Installs Docker and Docker Compose
3. Configures Artifact Registry auth
4. Uploads `docker-compose.prod.yml`, `init-gcp-sa.sh`, `.env.prod`
5. Pulls the sandbox image and tags it locally
6. Starts all services

### Step 5: Update HOST_IP

After the VM is created, update `.env.prod` with the external IP:

```bash
# Get the external IP
VM_IP=$(gcloud compute instances describe pomodex-prod \
    --zone=europe-west1-b \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)")
echo "VM IP: $VM_IP"

# Update .env.prod
sed -i '' "s/HOST_IP=.*/HOST_IP=$VM_IP/" .env.prod

# Re-upload and restart
gcloud compute scp .env.prod pomodex-prod:~/pomodex/.env --zone=europe-west1-b
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose up -d'
```

### Step 6: Configure Docker Daemon on VM

```bash
gcloud compute ssh pomodex-prod --zone=europe-west1-b --command='
  echo '"'"'{"default-address-pools":[{"base":"172.16.0.0/12","size":24}]}'"'"' | sudo tee /etc/docker/daemon.json
  sudo systemctl restart docker
  cd ~/pomodex && docker compose up -d
'
```

### Step 7: Verify

```bash
curl http://<VM_IP>/health
# Should return: {"status":"ok"}
```

---

## VM Operations

See [GCP_SETUP.md](GCP_SETUP.md) for the complete GCP commands reference.

```bash
# SSH into VM
gcloud compute ssh pomodex-prod --zone=europe-west1-b

# View logs
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose logs -f'

# View specific service logs
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose logs project-service --tail=100'

# Update with new images (after pushing)
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose pull && docker compose up -d'

# Restart a service
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- \
    'cd ~/pomodex && docker compose restart project-service'

# Get VM external IP
gcloud compute instances describe pomodex-prod \
    --zone=europe-west1-b \
    --format="get(networkInterfaces[0].accessConfigs[0].natIP)"

# Delete VM (destroys all data including postgres)
gcloud compute instances delete pomodex-prod --zone=europe-west1-b
```

## Updating a Single Service

To update just one service (e.g., `sandbox-web` after an nginx config change):

```bash
# Build and push just that image
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"  # macOS only
docker build --platform linux/amd64 \
    -t europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/sandbox-web:latest \
    sandbox-web
docker push europe-west1-docker.pkg.dev/pomodex-fd2bcd/pomodex/sandbox-web:latest

# Pull and restart on VM
gcloud compute ssh pomodex-prod --zone=europe-west1-b -- '
    cd ~/pomodex
    docker compose pull sandbox-web
    docker compose up -d sandbox-web
'
```

## Network & Ports

| Port | Service | Exposed? | Notes |
|------|---------|----------|-------|
| 80 | nginx (sandbox-web) | Yes (firewall rule) | Frontend + API + WebSocket proxy |
| 8000 | project-service | Internal only | Accessed via nginx |
| 9000 | terminal-proxy | Internal only | Accessed via project-service proxy |
| 5432 | postgres | Internal only | Database |
| 7681 | ttyd (per sandbox) | Internal only | Terminal WebSocket inside sandbox |
| 22 (sandbox) | sshd (per sandbox) | Dynamic host port | SSH access to sandboxes |

## Project Structure

```
pomodex/
├── backend/
│   ├── project_service/     # FastAPI backend
│   ├── terminal_proxy/      # WebSocket terminal proxy
│   └── sandbox/             # Sandbox container image
├── sandbox-web/             # React frontend + nginx
├── scripts/
│   ├── setup-local.sh       # Local dev startup
│   ├── push-images.sh       # Build & push to Artifact Registry
│   ├── deploy-vm.sh         # Create & deploy GCP VM
│   └── init-gcp-sa.sh       # Auto-create service account & secrets
├── docker-compose.yml       # Local dev (builds from source)
├── docker-compose.prod.yml  # Production (pre-built images from AR)
├── .env                     # Local env vars (gitignored)
├── .env.prod                # Production env vars (gitignored)
└── GCP_SETUP.md             # GCP commands reference
```
