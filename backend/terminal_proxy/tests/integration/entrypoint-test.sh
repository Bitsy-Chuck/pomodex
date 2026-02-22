#!/bin/bash
set -e

# Start Docker daemon in background
dockerd &
DOCKERD_PID=$!

# Wait for Docker daemon to be ready
echo "Waiting for Docker daemon..."
for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
        echo "Docker daemon ready."
        break
    fi
    sleep 1
done

if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon failed to start"
    exit 1
fi

# Start Squid
# Alpine Squid uses /run/squid.pid by default
squid -f /etc/squid/squid.conf
echo "Squid started."

# Wait for squid to write its PID file
for i in $(seq 1 10); do
    if [ -f /run/squid/squid.pid ] || [ -f /var/run/squid.pid ] || [ -f /run/squid.pid ]; then
        echo "Squid PID file found."
        break
    fi
    sleep 1
done

# Debug: find actual PID file
echo "Looking for Squid PID files..."
find / -name "squid.pid" -o -name "*.pid" 2>/dev/null | grep -i squid || echo "No squid pid files found"
ls -la /var/run/squid* /run/squid* 2>/dev/null || echo "No squid paths found"

# Symlink PID file to expected location if needed
if [ ! -f /var/run/squid.pid ]; then
    for f in /run/squid/squid.pid /run/squid.pid; do
        if [ -f "$f" ]; then
            ln -sf "$f" /var/run/squid.pid
            echo "Symlinked $f -> /var/run/squid.pid"
            break
        fi
    done
fi

echo "Final PID file state:"
ls -la /var/run/squid* /run/squid* 2>/dev/null || echo "NONE"
cat /var/run/squid.pid 2>/dev/null || echo "Cannot read /var/run/squid.pid"

# Build sandbox test image (GNU wget for proper HTTPS proxy + exit codes)
docker build -t sandbox-test -f /app/backend/terminal_proxy/tests/integration/Dockerfile.sandbox /app

# Run the integration tests
cd /app
python3 -m pytest backend/terminal_proxy/tests/integration/test_network_security.py -v -s --tb=long --timeout=60 -s
TEST_EXIT=$?

# Cleanup
kill $DOCKERD_PID 2>/dev/null || true
exit $TEST_EXIT
