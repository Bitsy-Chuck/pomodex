#!/usr/bin/env bash
set -euo pipefail

# Local dev: copy host gcloud config to writable location.
# GCP VM: metadata server provides credentials automatically.
if [ -d "/host-gcloud" ]; then
    cp -r /host-gcloud /tmp/gcloud-config
    export CLOUDSDK_CONFIG=/tmp/gcloud-config
fi

# --- JWT Secret ---
JWT_FILE="/secrets/jwt-secret"
if [ ! -f "$JWT_FILE" ]; then
    echo "Generating JWT secret..."
    openssl rand -hex 32 > "$JWT_FILE"
    chmod 400 "$JWT_FILE"
    echo "JWT secret saved to $JWT_FILE"
else
    echo "JWT secret already exists, skipping."
fi

# --- Internal Secret (for service-to-service auth) ---
INTERNAL_FILE="/secrets/internal-secret"
if [ ! -f "$INTERNAL_FILE" ]; then
    echo "Generating internal secret..."
    openssl rand -hex 32 > "$INTERNAL_FILE"
    chmod 400 "$INTERNAL_FILE"
    echo "Internal secret saved to $INTERNAL_FILE"
else
    echo "Internal secret already exists, skipping."
fi

# --- GCP Service Account ---
KEY_FILE="/secrets/project-service-sa.json"
SA_NAME="project-service-sa"
GCP_PROJECT="${GCP_PROJECT:-pomodex-fd2bcd}"
GCS_BUCKET="${GCS_BUCKET:-pomodex-fd2bcd-sandbox}"
SA_EMAIL="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

# If key already exists with real content, skip
if [ -f "$KEY_FILE" ] && grep -q '"type"' "$KEY_FILE" 2>/dev/null; then
    echo "SA key already exists, skipping."
    exit 0
fi

# Create SA if it doesn't exist
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$GCP_PROJECT" &>/dev/null; then
    echo "Creating service account $SA_NAME..."
    gcloud iam service-accounts create "$SA_NAME" \
        --project="$GCP_PROJECT" \
        --display-name="Agent Platform - Project Service"

    # Grant roles
    for role in roles/iam.serviceAccountAdmin roles/iam.serviceAccountKeyAdmin roles/storage.admin; do
        gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
            --member="serviceAccount:$SA_EMAIL" \
            --role="$role" \
            --quiet
    done

    # Grant storage admin on bucket
    gsutil iam ch "serviceAccount:${SA_EMAIL}:roles/storage.admin" "gs://${GCS_BUCKET}"

    # Grant AR writer (if repo exists)
    gcloud artifacts repositories add-iam-policy-binding sandboxes \
        --location=europe-west1 \
        --member="serviceAccount:$SA_EMAIL" \
        --role="roles/artifactregistry.writer" \
        --quiet 2>/dev/null || true
else
    echo "Service account $SA_EMAIL already exists."
fi

# Create and download key
echo "Creating SA key..."
gcloud iam service-accounts keys create "$KEY_FILE" \
    --iam-account="$SA_EMAIL" \
    --project="$GCP_PROJECT"

echo "Done. Key saved to $KEY_FILE"
