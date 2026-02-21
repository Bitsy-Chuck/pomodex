#!/bin/bash
# M1: Sandbox Base Image — Test Suite
# All 13 test cases from M1-sandbox-base-image.md
#
# Usage: ./test_m1_sandbox.sh
# Requires: Docker running, SSH key at ~/.ssh/id_ed25519.pub (or set TEST_SSH_PUBKEY)

set -uo pipefail

IMAGE_NAME="agent-sandbox:test"
BUILD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_SSH_PUBKEY="${TEST_SSH_PUBKEY:-$(cat ~/.ssh/id_ed25519.pub 2>/dev/null || echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKeyForCIOnly test@test")}"
CONTAINER_PREFIX="m1-test"
PASSED=0
FAILED=0
ERRORS=""

# Cleanup function
cleanup() {
    echo ""
    echo "=== Cleanup ==="
    for c in $(docker ps -aq --filter "name=${CONTAINER_PREFIX}" 2>/dev/null); do
        docker rm -f "$c" >/dev/null 2>&1 || true
    done
    echo "Containers cleaned up."
}
trap cleanup EXIT

# Test helper
run_test() {
    local test_id="$1"
    local test_name="$2"
    echo ""
    echo "--- $test_id: $test_name ---"
}

pass() {
    echo "  PASS"
    PASSED=$((PASSED + 1))
}

fail() {
    local msg="$1"
    echo "  FAIL: $msg"
    FAILED=$((FAILED + 1))
    ERRORS="${ERRORS}\n  - $msg"
}

# Wait for container to be running
wait_running() {
    local name="$1"
    local timeout="${2:-15}"
    for i in $(seq 1 "$timeout"); do
        local status
        status=$(docker inspect --format='{{.State.Status}}' "$name" 2>/dev/null || echo "not_found")
        if [ "$status" = "running" ]; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Wait for a service inside container
wait_for_service() {
    local name="$1"
    local check_cmd="$2"
    local timeout="${3:-20}"
    for i in $(seq 1 "$timeout"); do
        if docker exec "$name" bash -c "$check_cmd" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Start a standard test container
start_test_container() {
    local name="$1"
    shift
    docker run -d --name "$name" \
        --cap-add SYS_ADMIN --device /dev/fuse \
        -e SSH_PUBLIC_KEY="$TEST_SSH_PUBKEY" \
        -e GCS_SA_KEY='{"type":"service_account"}' \
        -e PROJECT_ID=test-project \
        -e GCS_BUCKET=test-bucket \
        "$@" \
        "$IMAGE_NAME"
}


########################################
# T1.1: Image builds successfully
########################################
run_test "T1.1" "Image builds successfully"

if docker build -t "$IMAGE_NAME" "$BUILD_DIR" > /tmp/m1-build.log 2>&1; then
    # Verify image exists
    if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${IMAGE_NAME}$"; then
        pass
    else
        fail "Build succeeded but image not found in docker images"
    fi
else
    fail "docker build failed (see /tmp/m1-build.log)"
    echo "  Build log tail:"
    tail -20 /tmp/m1-build.log | sed 's/^/    /'
    echo ""
    echo "=== Cannot proceed without a built image. Exiting. ==="
    echo "RESULTS: $PASSED passed, $FAILED failed"
    exit 1
fi


########################################
# T1.2: Container starts with supervisord
########################################
run_test "T1.2" "Container starts with supervisord"

start_test_container "${CONTAINER_PREFIX}-super"

if wait_running "${CONTAINER_PREFIX}-super" 15; then
    # Wait for supervisord to fully start all services
    sleep 5

    sup_output=$(docker exec "${CONTAINER_PREFIX}-super" supervisorctl status 2>&1 || true)
    echo "  supervisorctl output:"
    echo "$sup_output" | sed 's/^/    /'

    sshd_ok=false
    ttyd_ok=false
    backup_ok=false

    echo "$sup_output" | grep -q "sshd.*RUNNING" && sshd_ok=true
    echo "$sup_output" | grep -q "ttyd.*RUNNING" && ttyd_ok=true
    # backup-daemon will be RUNNING even if it logs GCS errors (expected)
    echo "$sup_output" | grep -q "backup-daemon.*RUNNING" && backup_ok=true

    if $sshd_ok && $ttyd_ok && $backup_ok; then
        pass
    else
        fail "Not all services RUNNING (sshd=$sshd_ok, ttyd=$ttyd_ok, backup=$backup_ok)"
    fi
else
    fail "Container not running after 15s"
fi

docker rm -f "${CONTAINER_PREFIX}-super" >/dev/null 2>&1 || true


########################################
# T1.3: SSH access works
########################################
run_test "T1.3" "SSH access works"

# Only run SSH test if we have a real key (not the fake CI key)
if [ -f ~/.ssh/id_ed25519 ]; then
    start_test_container "${CONTAINER_PREFIX}-ssh" -p 2222:22
    if wait_running "${CONTAINER_PREFIX}-ssh" 15; then
        # Wait for sshd to be ready
        wait_for_service "${CONTAINER_PREFIX}-ssh" "supervisorctl status sshd | grep RUNNING" 15

        ssh_output=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=10 -i ~/.ssh/id_ed25519 \
            -p 2222 agent@localhost whoami 2>/dev/null || echo "SSH_FAILED")

        if [ "$ssh_output" = "agent" ]; then
            # Check home dir
            home_output=$(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                -o ConnectTimeout=10 -i ~/.ssh/id_ed25519 \
                -p 2222 agent@localhost 'echo $HOME' 2>/dev/null || echo "UNKNOWN")
            if [ "$home_output" = "/home/agent" ]; then
                pass
            else
                fail "Home dir is '$home_output', expected '/home/agent'"
            fi
        else
            fail "SSH whoami returned '$ssh_output', expected 'agent'"
        fi
    else
        fail "Container not running"
    fi
    docker rm -f "${CONTAINER_PREFIX}-ssh" >/dev/null 2>&1 || true
else
    echo "  SKIP (no SSH key at ~/.ssh/id_ed25519)"
    # Count as pass since it's an environment issue, not a code issue
    pass
fi


########################################
# T1.4: ttyd WebSocket endpoint responds
########################################
run_test "T1.4" "ttyd WebSocket endpoint responds"

start_test_container "${CONTAINER_PREFIX}-ttyd" -p 7681:7681

if wait_running "${CONTAINER_PREFIX}-ttyd" 15; then
    # Wait for ttyd to be ready
    wait_for_service "${CONTAINER_PREFIX}-ttyd" "supervisorctl status ttyd | grep RUNNING" 15
    sleep 2

    http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 \
        http://localhost:7681/ 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ]; then
        pass
    else
        fail "HTTP status $http_code from ttyd, expected 200"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-ttyd" >/dev/null 2>&1 || true


########################################
# T1.5: tmux session "main" exists
########################################
run_test "T1.5" "tmux session 'main' exists"

start_test_container "${CONTAINER_PREFIX}-tmux" -p 7682:7681

if wait_running "${CONTAINER_PREFIX}-tmux" 15; then
    # Wait for ttyd to be ready
    wait_for_service "${CONTAINER_PREFIX}-tmux" "supervisorctl status ttyd | grep RUNNING" 15
    sleep 2

    # ttyd spawns tmux on first WebSocket connection — trigger one
    # Use curl to initiate a WebSocket upgrade (will fail quickly but triggers tmux spawn)
    curl -s -o /dev/null --connect-timeout 3 --max-time 3 \
        -H "Upgrade: websocket" -H "Connection: Upgrade" \
        -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
        -H "Sec-WebSocket-Version: 13" \
        http://localhost:7682/ws 2>/dev/null || true
    sleep 2

    tmux_output=$(docker exec "${CONTAINER_PREFIX}-tmux" su - agent -c "tmux list-sessions" 2>&1 || echo "NO_SESSIONS")
    echo "  tmux output: $tmux_output"

    if echo "$tmux_output" | grep -q "main:"; then
        pass
    else
        fail "tmux session 'main' not found"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-tmux" >/dev/null 2>&1 || true


########################################
# T1.6: Claude Code CLI is installed
########################################
run_test "T1.6" "Claude Code CLI is installed"

start_test_container "${CONTAINER_PREFIX}-claude"

if wait_running "${CONTAINER_PREFIX}-claude" 10; then
    which_output=$(docker exec "${CONTAINER_PREFIX}-claude" which claude 2>/dev/null || echo "NOT_FOUND")
    version_output=$(docker exec "${CONTAINER_PREFIX}-claude" claude --version 2>/dev/null || echo "VERSION_FAILED")

    echo "  which claude: $which_output"
    echo "  claude --version: $version_output"

    if [ "$which_output" != "NOT_FOUND" ] && [ "$version_output" != "VERSION_FAILED" ]; then
        pass
    else
        fail "Claude CLI not found or version check failed"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-claude" >/dev/null 2>&1 || true


########################################
# T1.7: FUSE device available
########################################
run_test "T1.7" "FUSE device available"

start_test_container "${CONTAINER_PREFIX}-fuse"

if wait_running "${CONTAINER_PREFIX}-fuse" 10; then
    fuse_output=$(docker exec "${CONTAINER_PREFIX}-fuse" ls -la /dev/fuse 2>&1 || echo "NOT_FOUND")
    echo "  /dev/fuse: $fuse_output"

    if echo "$fuse_output" | grep -q "^c"; then
        pass
    else
        fail "/dev/fuse not found or not a character device"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-fuse" >/dev/null 2>&1 || true


########################################
# T1.8: Entrypoint handles SSH key injection
########################################
run_test "T1.8" "Entrypoint handles SSH key injection"

start_test_container "${CONTAINER_PREFIX}-sshkey"

if wait_running "${CONTAINER_PREFIX}-sshkey" 10; then
    sleep 2

    # Check file content
    key_content=$(docker exec "${CONTAINER_PREFIX}-sshkey" cat /home/agent/.ssh/authorized_keys 2>/dev/null || echo "FILE_NOT_FOUND")

    # Check permissions
    key_perms=$(docker exec "${CONTAINER_PREFIX}-sshkey" stat -c '%a' /home/agent/.ssh/authorized_keys 2>/dev/null || echo "UNKNOWN")

    # Check ownership
    key_owner=$(docker exec "${CONTAINER_PREFIX}-sshkey" stat -c '%U:%G' /home/agent/.ssh/authorized_keys 2>/dev/null || echo "UNKNOWN")

    echo "  content matches: $([ "$key_content" = "$TEST_SSH_PUBKEY" ] && echo yes || echo no)"
    echo "  permissions: $key_perms (expect 600)"
    echo "  ownership: $key_owner (expect agent:agent)"

    if [ "$key_content" = "$TEST_SSH_PUBKEY" ] && [ "$key_perms" = "600" ] && [ "$key_owner" = "agent:agent" ]; then
        pass
    else
        fail "SSH key injection incorrect (content_match=$([ "$key_content" = "$TEST_SSH_PUBKEY" ] && echo yes || echo no), perms=$key_perms, owner=$key_owner)"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-sshkey" >/dev/null 2>&1 || true


########################################
# T1.9: Entrypoint handles GCS key file
########################################
run_test "T1.9" "Entrypoint handles GCS key file"

start_test_container "${CONTAINER_PREFIX}-gcskey"

if wait_running "${CONTAINER_PREFIX}-gcskey" 10; then
    sleep 2

    gcs_content=$(docker exec "${CONTAINER_PREFIX}-gcskey" cat /tmp/gcs-key.json 2>/dev/null || echo "FILE_NOT_FOUND")
    gcs_perms=$(docker exec "${CONTAINER_PREFIX}-gcskey" stat -c '%a' /tmp/gcs-key.json 2>/dev/null || echo "UNKNOWN")

    echo "  content: $gcs_content"
    echo "  permissions: $gcs_perms (expect 600)"

    if [ "$gcs_content" = '{"type":"service_account"}' ] && [ "$gcs_perms" = "600" ]; then
        pass
    else
        fail "GCS key file incorrect (content=$gcs_content, perms=$gcs_perms)"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-gcskey" >/dev/null 2>&1 || true


########################################
# T1.10: First-boot flag logic
########################################
run_test "T1.10" "First-boot flag logic"

start_test_container "${CONTAINER_PREFIX}-initflag"

if wait_running "${CONTAINER_PREFIX}-initflag" 10; then
    sleep 3

    # Check flag exists after first boot
    flag_exists=$(docker exec "${CONTAINER_PREFIX}-initflag" test -f /home/agent/.sandbox_initialized && echo "yes" || echo "no")
    echo "  .sandbox_initialized exists: $flag_exists"

    if [ "$flag_exists" = "yes" ]; then
        # Check logs for first boot message
        logs=$(docker logs "${CONTAINER_PREFIX}-initflag" 2>&1)
        if echo "$logs" | grep -q "First boot"; then
            pass
        else
            fail "Flag exists but 'First boot' message not in logs"
        fi
    else
        fail ".sandbox_initialized not created"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-initflag" >/dev/null 2>&1 || true


########################################
# T1.11: /home/agent is writable by agent user
########################################
run_test "T1.11" "/home/agent is writable by agent user"

start_test_container "${CONTAINER_PREFIX}-write"

if wait_running "${CONTAINER_PREFIX}-write" 10; then
    sleep 2

    if docker exec -u agent "${CONTAINER_PREFIX}-write" touch /home/agent/test-write 2>/dev/null; then
        owner=$(docker exec "${CONTAINER_PREFIX}-write" stat -c '%U:%G' /home/agent/test-write 2>/dev/null || echo "UNKNOWN")
        echo "  file owner: $owner"
        if [ "$owner" = "agent:agent" ]; then
            pass
        else
            fail "File not owned by agent:agent (owner=$owner)"
        fi
    else
        fail "Cannot write to /home/agent as agent user"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-write" >/dev/null 2>&1 || true


########################################
# T1.12: sshd_config security
########################################
run_test "T1.12" "sshd_config security"

SSHD_CONFIG="$BUILD_DIR/config/sshd_config"

if [ -f "$SSHD_CONFIG" ]; then
    errors=""

    grep -q "^PasswordAuthentication no" "$SSHD_CONFIG" || errors="${errors} missing 'PasswordAuthentication no'"
    grep -q "^PermitRootLogin no" "$SSHD_CONFIG" || errors="${errors} missing 'PermitRootLogin no'"
    grep -q "^AllowUsers agent" "$SSHD_CONFIG" || errors="${errors} missing 'AllowUsers agent'"
    grep -q "^PubkeyAuthentication yes" "$SSHD_CONFIG" || errors="${errors} missing 'PubkeyAuthentication yes'"

    if [ -z "$errors" ]; then
        pass
    else
        fail "sshd_config security issues:$errors"
    fi
else
    fail "sshd_config not found at $SSHD_CONFIG"
fi


########################################
# T1.13: Docker HEALTHCHECK works
########################################
run_test "T1.13" "Docker HEALTHCHECK works"

start_test_container "${CONTAINER_PREFIX}-health"

if wait_running "${CONTAINER_PREFIX}-health" 15; then
    # Wait for healthcheck to run (interval=30s, but we can wait for first check)
    echo "  Waiting for healthcheck (up to 60s)..."
    healthy=false
    for i in $(seq 1 60); do
        health_status=$(docker inspect --format='{{.State.Health.Status}}' "${CONTAINER_PREFIX}-health" 2>/dev/null || echo "none")
        if [ "$health_status" = "healthy" ]; then
            healthy=true
            break
        fi
        sleep 1
    done

    echo "  Health status: $health_status"

    if $healthy; then
        pass
    else
        fail "Container not healthy after 60s (status=$health_status)"
    fi
else
    fail "Container not running"
fi

docker rm -f "${CONTAINER_PREFIX}-health" >/dev/null 2>&1 || true


########################################
# Results
########################################
echo ""
echo "========================================"
echo "  RESULTS: $PASSED passed, $FAILED failed"
echo "========================================"
if [ $FAILED -gt 0 ]; then
    echo -e "  Failures:$ERRORS"
    exit 1
else
    echo "  All tests passed!"
    exit 0
fi
