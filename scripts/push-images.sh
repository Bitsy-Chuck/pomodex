#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-europe-west1}"
PROJECT="${GCP_PROJECT:-pomodex-fd2bcd}"
REPO_NAME="${AR_REPO:-pomodex}"
TAG="${IMAGE_TAG:-latest}"

AR_PREFIX="${REGION}-docker.pkg.dev/${PROJECT}/${REPO_NAME}"

# Create AR repo if it doesn't exist
gcloud artifacts repositories describe "$REPO_NAME" \
    --location="$REGION" --project="$PROJECT" &>/dev/null || \
gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT"

# Auth docker to AR
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

images=(
    "project-service:backend/project_service"
    "terminal-proxy:backend/terminal_proxy"
    "agent-sandbox:backend/sandbox"
    "sandbox-web:sandbox-web"
)

for entry in "${images[@]}"; do
    name="${entry%%:*}"
    context="${entry#*:}"
    full_tag="${AR_PREFIX}/${name}:${TAG}"

    echo "==> Building ${name} (linux/amd64)..."
    docker build --platform linux/amd64 -t "$full_tag" "${ROOT_DIR}/${context}"

    echo "==> Pushing ${full_tag}..."
    docker push "$full_tag"
done

echo "Done. All images pushed to ${AR_PREFIX}/*:${TAG}"
