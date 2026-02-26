#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
VM_NAME="${1:?Usage: deploy-vm.sh <vm-name> [zone]}"
ZONE="${2:-europe-west1-b}"
PROJECT="${GCP_PROJECT:-pomodex-fd2bcd}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-medium}"
REGION="${REGION:-europe-west1}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# --- Check required files ---
for f in docker-compose.prod.yml scripts/init-gcp-sa.sh; do
    [ -f "${ROOT_DIR}/${f}" ] || { echo "Missing ${f}"; exit 1; }
done

if [ ! -f "${ROOT_DIR}/.env.prod" ]; then
    echo "Missing .env.prod — create it with at least POSTGRES_PASSWORD set."
    echo "Example:"
    echo "  GCP_PROJECT=pomodex-fd2bcd"
    echo "  GCS_BUCKET=pomodex-fd2bcd-sandbox"
    echo "  POSTGRES_PASSWORD=<strong-password>"
    echo "  CORS_ORIGINS=*"
    exit 1
fi

echo "==> Creating VM ${VM_NAME} in ${ZONE}..."
gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=30GB \
    --tags=http-server \
    --scopes=cloud-platform

echo "==> Waiting for SSH..."
for i in $(seq 1 30); do
    gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
        --command="echo ready" &>/dev/null && break
    sleep 5
done

echo "==> Installing Docker..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command='
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo systemctl enable docker
    sudo usermod -aG docker $USER
'

echo "==> Configuring Artifact Registry auth..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
    gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet
"

echo "==> Uploading files..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
    mkdir -p ~/pomodex/scripts
"
gcloud compute scp "${ROOT_DIR}/docker-compose.prod.yml" "${VM_NAME}:~/pomodex/docker-compose.yml" \
    --zone="$ZONE" --project="$PROJECT"
gcloud compute scp "${ROOT_DIR}/scripts/init-gcp-sa.sh" "${VM_NAME}:~/pomodex/scripts/init-gcp-sa.sh" \
    --zone="$ZONE" --project="$PROJECT"
gcloud compute scp "${ROOT_DIR}/.env.prod" "${VM_NAME}:~/pomodex/.env" \
    --zone="$ZONE" --project="$PROJECT"

echo "==> Pulling sandbox image and tagging..."
AR_PREFIX="${REGION}-docker.pkg.dev/${PROJECT}/pomodex"
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
    newgrp docker <<EOF
    docker pull ${AR_PREFIX}/agent-sandbox:latest
    docker tag ${AR_PREFIX}/agent-sandbox:latest agent-sandbox:latest
EOF
"

echo "==> Starting services..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="
    newgrp docker <<EOF
    cd ~/pomodex && docker compose up -d
EOF
"

EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" --project="$PROJECT" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "==> Done! VM running at ${EXTERNAL_IP}"
echo "    SSH:  gcloud compute ssh ${VM_NAME} --zone=${ZONE}"
echo "    Logs: gcloud compute ssh ${VM_NAME} --zone=${ZONE} -- 'cd ~/pomodex && docker compose logs -f'"
