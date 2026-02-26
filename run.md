# Running Pomodex

## Prerequisites

- Docker Desktop (with Compose v2)
- `gcloud` CLI authenticated (`gcloud auth login`)
- The sandbox base image built locally:
  ```bash
  docker build -t agent-sandbox:latest ./backend/sandbox
  ```

## Quick Start

```bash
docker compose up
```

On **first run**, the `init-gcp-secrets` service will automatically:
1. Generate a production-grade JWT signing secret (persisted across restarts)
2. Create a GCP service account (`project-service-sa`)
3. Grant it the required IAM roles
4. Download the SA key

On **subsequent runs**, it detects existing secrets and skips (< 1 second).

Services started:
| Service | Port | Description |
|---------|------|-------------|
| project-service | 8000 | FastAPI backend (auth, projects, sandboxes) |
| terminal-proxy | 9000 | WebSocket terminal proxy to sandbox containers |
| postgres | 5432 | PostgreSQL database (internal) |

## Web Client (dev)

```bash
cd sandbox-web
npm install
npm run dev
```

Opens at `http://localhost:5173` by default.

## Secrets

All secrets live in the `secrets-data` Docker volume, created automatically on first boot:

| File | Purpose |
|------|---------|
| `jwt-secret` | 256-bit HMAC key for signing JWTs |
| `project-service-sa.json` | GCP service account key |

To reset secrets and force regeneration:
```bash
docker compose down
docker volume rm pomodex_secrets-data
docker compose up
```

## Environment Overrides

These can be set in `.env` or exported before `docker compose up`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GCP_PROJECT` | `pomodex-fd2bcd` | GCP project ID |
| `GCS_BUCKET` | `pomodex-fd2bcd-sandbox` | GCS bucket for project storage |
| `SANDBOX_IMAGE` | `agent-sandbox:latest` | Docker image for sandbox containers |
| `POSTGRES_PASSWORD` | `pomodex` | PostgreSQL password |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |

## Stopping

```bash
docker compose down          # stop containers, keep data
docker compose down -v       # stop containers AND delete all volumes (full reset)
```
