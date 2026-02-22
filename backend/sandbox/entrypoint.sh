#!/bin/bash
set -e

# --- Signal handling ---
# Forward SIGTERM to child processes so Docker stop works during init.
# Once supervisord takes over via exec, it handles signals itself.
_term() {
    echo "SIGTERM during init, shutting down children..."
    # Kill all child processes of PID 1
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    exit 0
}
trap _term SIGTERM SIGINT

# --- SSH key ---
echo "$SSH_PUBLIC_KEY" > /home/agent/.ssh/authorized_keys
chmod 600 /home/agent/.ssh/authorized_keys
chown agent:agent /home/agent/.ssh/authorized_keys

# --- GCS service account key ---
echo "$GCS_SA_KEY" > /tmp/gcs-key.json
chmod 640 /tmp/gcs-key.json
chown root:agent /tmp/gcs-key.json
export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcs-key.json

# --- Default backup interval (overridable via env) ---
export BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-300}"

# --- Enable FUSE allow_other ---
echo "user_allow_other" >> /etc/fuse.conf

# --- Mount GCS (project-scoped, read-write) ---
mkdir -p /mnt/gcs
gcsfuse \
    --key-file=/tmp/gcs-key.json \
    --uid=$(id -u agent) --gid=$(id -g agent) \
    --implicit-dirs \
    -o allow_other \
    --only-dir="${PROJECT_ID}" \
    "${GCS_BUCKET}" /mnt/gcs 2>/dev/null &
wait $! 2>/dev/null || echo "gcsfuse project mount failed (expected without real GCS)"

# --- First-boot restore from GCS ---
INIT_FLAG="/home/agent/.sandbox_initialized"
if [ ! -f "$INIT_FLAG" ]; then
    echo "First boot: checking for GCS backup..."
    if rclone ls ":gcs:${GCS_BUCKET}/${PROJECT_ID}/workspace" \
       --contimeout=5s --timeout=10s 2>/dev/null | grep -q .; then
        echo "Backup found — restoring..."
        rclone sync \
            ":gcs:${GCS_BUCKET}/${PROJECT_ID}/workspace" \
            /home/agent \
            --transfers=8 --checksum
    else
        echo "No backup found — fresh start."
    fi
    touch "$INIT_FLAG"
    chown agent:agent "$INIT_FLAG"
fi

# --- Pre-create tmux session for agent ---
# ttyd uses "tmux new-session -A -s main" which attaches to existing or creates new.
# Pre-creating ensures the session exists immediately, not only after first WebSocket connect.
su - agent -c "tmux new-session -d -s main" || true

exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
