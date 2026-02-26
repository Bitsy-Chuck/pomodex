#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> Building agent-sandbox image..."
docker build -t agent-sandbox:latest "${ROOT_DIR}/backend/sandbox"

echo "==> Starting all services..."
docker compose -f "${ROOT_DIR}/docker-compose.yml" up --build "$@"
