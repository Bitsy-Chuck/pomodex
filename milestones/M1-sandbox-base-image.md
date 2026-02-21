# M1: Sandbox Base Image & Runtime Validation

| Field | Value |
|-------|-------|
| **Risk** | CRITICAL |
| **Dependencies** | None |
| **Blocks** | M2, M4 |
| **Plan sections** | 6 (Sandbox Base Image) |

---

## Objective

Build the Docker image that runs inside each sandbox container. Verify that all
services (sshd, ttyd, tmux, backup daemon) start and work together under
supervisord. This is the foundation — every other milestone depends on it.

---

## Why This Is First

If the base image doesn't build or services conflict, nothing else works.
Risk areas:
- gcsfuse requires FUSE + SYS_ADMIN cap — may not work in Docker without tuning
- ttyd binary download could break (architecture mismatch, URL change)
- supervisord managing 4 processes simultaneously — startup ordering issues
- Claude Code CLI install via npm — version pinning, binary compatibility

---

## Scope

**In scope:**
- Dockerfile (Ubuntu 24.04 base)
- entrypoint.sh (SSH key injection, GCS key injection, gcsfuse mount, first-boot restore, supervisord exec)
- supervisord.conf (sshd, ttyd, backup-daemon)
- sshd_config
- backup_daemon.py (just the script — GCS integration tested in M2)
- Agent user setup (home dir, SSH dir, permissions)

**Out of scope:**
- GCS bucket/IAM setup (M2, M3)
- Docker SDK lifecycle management (M4)
- Networking/iptables (M6)

---

## Deliverables

```
backend/sandbox/
  Dockerfile
  entrypoint.sh
  config/sshd_config
  config/supervisord.conf
  scripts/backup_daemon.py
```

---

## Implementation Tasks

1. Write `sshd_config` — allow only key-based auth, agent user only
2. Write `supervisord.conf` — sshd, ttyd, backup-daemon programs
3. Write `backup_daemon.py` — loop with rclone sync, logging, error handling
4. Write `entrypoint.sh` — SSH key injection, GCS key file, gcsfuse mounts (will fail gracefully without real GCS), init flag logic, exec supervisord
5. Write `Dockerfile` — Ubuntu 24.04, install all packages, create agent user, copy configs, add HEALTHCHECK
6. Build image and iterate until all tests pass

---

## Test Cases

### T1.1: Image builds successfully
**Type**: Build test
**Command**: `docker build -t agent-sandbox:test ./backend/sandbox`
**Assert**:
- Exit code 0
- Image tagged `agent-sandbox:test` exists in `docker images`

### T1.2: Container starts with supervisord
**Type**: Integration (Docker)
**Setup**: Run container with minimal env vars (SSH_PUBLIC_KEY, dummy GCS_SA_KEY, PROJECT_ID, GCS_BUCKET)
**Command**:
```bash
docker run -d --name test-sandbox \
  --cap-add SYS_ADMIN --device /dev/fuse \
  -e SSH_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  -e GCS_SA_KEY='{"type":"service_account"}' \
  -e PROJECT_ID=test-project \
  -e GCS_BUCKET=test-bucket \
  agent-sandbox:test
```
**Assert**:
- Container status is "running" after 10 seconds
- `docker exec test-sandbox supervisorctl status` shows all programs
- sshd: RUNNING
- ttyd: RUNNING
- backup-daemon: RUNNING (will log errors about GCS, that's expected)

### T1.3: SSH access works
**Type**: Integration (Docker)
**Setup**: Container running with SSH_PUBLIC_KEY set to test key, port 22 mapped
**Command**:
```bash
docker run -d --name test-ssh -p 2222:22 \
  --cap-add SYS_ADMIN --device /dev/fuse \
  -e SSH_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)" \
  -e GCS_SA_KEY='{}' -e PROJECT_ID=test -e GCS_BUCKET=test \
  agent-sandbox:test

ssh -o StrictHostKeyChecking=no -p 2222 agent@localhost whoami
```
**Assert**:
- SSH connection succeeds
- Output: `agent`
- Home directory is `/home/agent`
- Agent user can write files to `/home/agent`

### T1.4: ttyd WebSocket endpoint responds
**Type**: Integration (Docker)
**Setup**: Container running with ttyd on port 7681
**Command**:
```bash
# Map ttyd port for testing
docker run -d --name test-ttyd -p 7681:7681 \
  --cap-add SYS_ADMIN --device /dev/fuse \
  -e SSH_PUBLIC_KEY="ssh-ed25519 AAAA test" \
  -e GCS_SA_KEY='{}' -e PROJECT_ID=test -e GCS_BUCKET=test \
  agent-sandbox:test

# Wait for ttyd to start
sleep 3

# Check HTTP response (ttyd serves its built-in web page)
curl -s -o /dev/null -w "%{http_code}" http://localhost:7681/
```
**Assert**:
- HTTP status 200 from ttyd's built-in page
- WebSocket upgrade at `ws://localhost:7681/ws` accepted

### T1.5: tmux session "main" exists
**Type**: Integration (Docker)
**Setup**: Container running
**Command**:
```bash
docker exec test-sandbox su - agent -c "tmux list-sessions"
```
**Assert**:
- Output contains `main:` (the named session)
- Session is attached to a bash shell

### T1.6: Claude Code CLI is installed
**Type**: Integration (Docker)
**Command**:
```bash
docker exec test-sandbox which claude
docker exec test-sandbox claude --version
```
**Assert**:
- `which claude` returns a path (e.g., `/usr/local/bin/claude`)
- `claude --version` outputs a version string without error

### T1.7: FUSE device available
**Type**: Integration (Docker)
**Command**:
```bash
docker exec test-sandbox ls -la /dev/fuse
```
**Assert**:
- `/dev/fuse` exists and is a character device
- This confirms `--device /dev/fuse` is working

### T1.8: Entrypoint handles SSH key injection
**Type**: Integration (Docker)
**Command**:
```bash
docker exec test-sandbox cat /home/agent/.ssh/authorized_keys
```
**Assert**:
- File exists with permissions 600
- Content matches the SSH_PUBLIC_KEY env var value
- Owned by agent:agent

### T1.9: Entrypoint handles GCS key file
**Type**: Integration (Docker)
**Command**:
```bash
docker exec test-sandbox cat /tmp/gcs-key.json
```
**Assert**:
- File exists with permissions 600
- Content matches the GCS_SA_KEY env var value
- GOOGLE_APPLICATION_CREDENTIALS env var is set to `/tmp/gcs-key.json`

### T1.10: First-boot flag logic
**Type**: Integration (Docker)
**Setup**: Run container without `.sandbox_initialized` in volume
**Command**:
```bash
docker exec test-sandbox cat /home/agent/.sandbox_initialized
```
**Assert**:
- File exists after first boot (entrypoint creates it)
- On second container start with same volume, entrypoint skips restore step
  (verify via container logs: should NOT see "First boot: checking for GCS backup")

### T1.11: /home/agent is writable by agent user
**Type**: Integration (Docker)
**Command**:
```bash
docker exec -u agent test-sandbox touch /home/agent/test-write
docker exec -u agent test-sandbox ls -la /home/agent/test-write
```
**Assert**:
- File created successfully
- Owned by agent:agent

### T1.12: sshd_config security
**Type**: Unit (config validation)
**Assert** (by reading the file):
- `PasswordAuthentication no`
- `PermitRootLogin no`
- `AllowUsers agent`
- `PubkeyAuthentication yes`

### T1.13: Docker HEALTHCHECK works
**Type**: Integration (Docker)
**Steps**:
1. Start container, wait for all services to start
2. Check `docker inspect --format='{{.State.Health.Status}}' test-sandbox`
**Assert**:
- Health status is "healthy" when all supervisord services are RUNNING
- If ttyd is killed manually, health transitions to "unhealthy" after retries

---

## Acceptance Criteria

- [ ] `docker build` succeeds on both x86_64 and arm64 (or at minimum x86_64 for GCP)
- [ ] All 13 test cases pass
- [ ] Container stays running for 5+ minutes without crashes (supervisord stable)
- [ ] HEALTHCHECK reports healthy when all services are running
- [ ] No error logs from supervisord except expected GCS failures (no real bucket yet)
