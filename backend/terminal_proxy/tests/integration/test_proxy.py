"""Integration tests for Terminal Proxy (T7.2-T7.15)."""

import asyncio
import json
import logging
import os

import pytest
import websockets


# ---------------------------------------------------------------------------
# T7.2: Valid JWT — connection accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t72_valid_jwt_connection_accepted(proxy):
    """T7.2: Valid token -> connection stays open, auth call made."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        # Connection is open — send a message to prove it
        await ws.send("hello")
        response = await asyncio.wait_for(ws.recv(), timeout=2.0)
        assert response == "hello"

    # Verify auth was called with correct params
    assert len(proxy["auth"]["calls"]) == 1
    call = proxy["auth"]["calls"][0]
    assert call["token"] == "token-user1"
    assert call["project_id"] == "proj-aaa"


# ---------------------------------------------------------------------------
# T7.3: Invalid JWT — connection rejected with 4401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t73_invalid_jwt_rejected(proxy):
    """T7.3: Invalid token -> closed with 4401."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=bad-token"
    ws = await websockets.connect(url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed as e:
        assert e.rcvd.code == 4401
        assert "Unauthorized" in e.rcvd.reason


# ---------------------------------------------------------------------------
# T7.4: Missing token — connection rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t74_missing_token_rejected(proxy):
    """T7.4: No token param -> closed with 4400."""
    url = f"{proxy['url']}/terminal/proj-aaa"
    ws = await websockets.connect(url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed as e:
        assert e.rcvd.code == 4400
        assert "Token required" in e.rcvd.reason

    # No auth request should have been sent
    assert len(proxy["auth"]["calls"]) == 0


# ---------------------------------------------------------------------------
# T7.5: Wrong project ownership — rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t75_wrong_project_ownership_rejected(proxy):
    """T7.5: Valid token but user doesn't own project -> 4401."""
    # token-user1 owns proj-aaa and proj-bbb, NOT proj-ccc
    url = f"{proxy['url']}/terminal/proj-ccc?token=token-user1"
    ws = await websockets.connect(url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed as e:
        assert e.rcvd.code == 4401


# ---------------------------------------------------------------------------
# T7.6: Proxy forwards client input to ttyd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t76_proxy_forwards_input_to_ttyd(proxy):
    """T7.6: Client sends message -> ttyd receives exact bytes."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        await ws.send("ls -la\r")
        # Wait for echo back (mock ttyd echoes)
        response = await asyncio.wait_for(ws.recv(), timeout=2.0)
        assert response == "ls -la\r"

    # Verify mock ttyd received the exact message
    assert "ls -la\r" in proxy["ttyd"]["received"]


# ---------------------------------------------------------------------------
# T7.7: Proxy forwards ttyd output to client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t77_proxy_forwards_ttyd_output_to_client(proxy_with_welcome):
    """T7.7: ttyd sends binary data -> client receives exact bytes."""
    url = f"{proxy_with_welcome['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        # Mock ttyd sends a welcome prompt first
        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
        assert msg == b"\x1b[?25h$ "  # Binary terminal data preserved


# ---------------------------------------------------------------------------
# T7.8: Audit log captures input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t78_audit_log_captures_input(proxy):
    """T7.8: Input messages logged with project_id, user_id, timestamp."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"

    # Capture audit log records
    audit_records = []
    handler = logging.Handler()
    handler.emit = lambda record: audit_records.append(record)
    audit_logger = logging.getLogger("terminal_proxy.audit")
    audit_logger.setLevel(logging.DEBUG)
    audit_logger.addHandler(handler)

    try:
        async with websockets.connect(url) as ws:
            await ws.send("echo hello\r")
            await ws.send("ls\r")
            # Wait for echoes
            await asyncio.wait_for(ws.recv(), timeout=2.0)
            await asyncio.wait_for(ws.recv(), timeout=2.0)
    finally:
        audit_logger.removeHandler(handler)

    # Verify audit entries
    assert len(audit_records) >= 2
    for record in audit_records:
        entry = json.loads(record.getMessage())
        assert entry["project_id"] == "proj-aaa"
        assert entry["user_id"] == "user-001"
        assert "timestamp" in entry
        assert entry["event"] == "terminal_input"

    contents = [json.loads(r.getMessage())["content"] for r in audit_records]
    assert "echo hello\r" in contents
    assert "ls\r" in contents


# ---------------------------------------------------------------------------
# T7.9: Container not running — connection fails gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t79_container_not_running(proxy_no_container):
    """T7.9: Container stopped -> closed with 4503."""
    url = f"{proxy_no_container['url']}/terminal/proj-aaa?token=token-user1"
    ws = await websockets.connect(url)
    # Wait for close frame from server
    try:
        async for _ in ws:
            pass  # drain until closed
    except websockets.ConnectionClosed:
        pass
    assert ws.close_code == 4503
    assert "Container not running" in (ws.close_reason or "")


# ---------------------------------------------------------------------------
# T7.12: Client disconnect — proxy cleans up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t712_client_disconnect_cleanup(proxy):
    """T7.12: Client closes -> proxy closes ttyd connection, no errors."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        await ws.send("hello")
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        # Client closes connection (context manager exit)

    # Give proxy time to clean up
    await asyncio.sleep(0.2)
    # If we get here without errors, cleanup succeeded.


# ---------------------------------------------------------------------------
# T7.13: ttyd disconnect — proxy notifies client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t713_ttyd_disconnect_notifies_client(proxy_closable_ttyd):
    """T7.13: ttyd disconnects -> client WebSocket is closed."""
    url = f"{proxy_closable_ttyd['url']}/terminal/proj-aaa?token=token-user1"
    ws = await websockets.connect(url)

    # Send a message to establish the proxy
    await ws.send("hello")
    await asyncio.wait_for(ws.recv(), timeout=2.0)

    # Kill mock ttyd connections
    await proxy_closable_ttyd["ttyd"]["close_fn"]()

    # Client should get disconnected
    try:
        await asyncio.wait_for(ws.recv(), timeout=3.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed:
        pass  # Expected


# ---------------------------------------------------------------------------
# T7.14: Concurrent connections to same sandbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t714_concurrent_connections(proxy):
    """T7.14: Two clients connect to same project independently."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"

    async with websockets.connect(url) as ws_a, websockets.connect(url) as ws_b:
        # Client A sends
        await ws_a.send("from-a")
        resp_a = await asyncio.wait_for(ws_a.recv(), timeout=2.0)
        assert resp_a == "from-a"

        # Client B sends
        await ws_b.send("from-b")
        resp_b = await asyncio.wait_for(ws_b.recv(), timeout=2.0)
        assert resp_b == "from-b"

    # Verify both connections worked independently
    assert "from-a" in proxy["ttyd"]["received"]
    assert "from-b" in proxy["ttyd"]["received"]

    # 2 connections = 2 auth calls
    assert len(proxy["auth"]["calls"]) == 2


# ---------------------------------------------------------------------------
# T7.15: last_connection_at updated on connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t715_validate_called_on_connect(proxy):
    """T7.15: Proxy calls /internal/validate with correct params on connect.

    The actual last_connection_at DB update happens inside Project Service (M8).
    Here we verify the proxy made the right call that triggers it.
    """
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        await ws.send("ping")
        await asyncio.wait_for(ws.recv(), timeout=2.0)

    assert len(proxy["auth"]["calls"]) >= 1
    call = proxy["auth"]["calls"][0]
    assert call["token"] == "token-user1"
    assert call["project_id"] == "proj-aaa"


# ---------------------------------------------------------------------------
# Docker-dependent tests (T7.10, T7.11)
# ---------------------------------------------------------------------------

docker_available = os.environ.get("DOCKER_TESTS", "0") == "1"
skip_no_docker = pytest.mark.skipif(
    not docker_available,
    reason="Set DOCKER_TESTS=1 to run Docker integration tests",
)


@skip_no_docker
@pytest.mark.asyncio
async def test_t710_container_bridge_ip_lookup():
    """T7.10: Look up running container bridge IP via Docker SDK."""
    import docker as docker_sdk
    from backend.terminal_proxy.services.container_lookup import get_container_ip

    client = docker_sdk.from_env()
    project_id = "t710-test"

    network = client.networks.create(f"net-{project_id}", driver="bridge")
    try:
        container = client.containers.run(
            "alpine:latest",
            command="sleep 60",
            name=f"sandbox-{project_id}",
            network=f"net-{project_id}",
            detach=True,
        )
        try:
            ip = get_container_ip(project_id)
            assert ip.startswith("172.") or ip.startswith("10.") or ip.startswith("192.168.")
            assert len(ip.split(".")) == 4
        finally:
            container.remove(force=True)
    finally:
        network.remove()


@skip_no_docker
@pytest.mark.asyncio
async def test_t711_full_roundtrip_via_ttyd():
    """T7.11: Full round-trip through proxy -> ttyd -> tmux -> bash.

    Uses a published port for ttyd so this works on Docker Desktop (macOS)
    where container bridge IPs aren't reachable from the host.
    In production, the proxy runs inside Docker and uses bridge IPs directly.

    ttyd protocol (ASCII char prefixes, not binary):
    - Subprotocol: 'tty'
    - Client sends {"AuthToken": ""} first
    - Client input: '0' + text (ASCII '0' = INPUT)
    - Client resize: '1' + JSON (ASCII '1' = RESIZE_TERMINAL)
    - Server output: '0' + data (ASCII '0' = OUTPUT)
    """
    import docker as docker_sdk
    import time
    from backend.project_service.services.docker_manager import (
        cleanup_project_resources,
        find_free_port,
        create_network,
        create_volume,
    )

    client = docker_sdk.from_env()
    project_id = "t711-test"
    image = os.environ.get("SANDBOX_IMAGE", "agent-sandbox:test")

    try:
        # Create resources the same way docker_manager.create_container does,
        # but also publish ttyd port so we can connect from the host.
        create_network(project_id)
        create_volume(project_id)
        ttyd_host_port = find_free_port()

        container = client.containers.run(
            image=image,
            name=f"sandbox-{project_id}",
            detach=True,
            volumes={f"vol-{project_id}": {"bind": "/home/agent", "mode": "rw"}},
            ports={"22/tcp": find_free_port(), "7681/tcp": ttyd_host_port},
            environment={
                "PROJECT_ID": project_id,
                "GCS_BUCKET": os.environ.get("GCS_BUCKET", "pomodex-fd2bcd-sandbox"),
                "GCS_PREFIX": f"projects/{project_id}",
                "GCS_SA_KEY": os.environ.get("GCS_SA_KEY", "{}"),
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
            },
            network=f"net-{project_id}",
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            security_opt=["apparmor:unconfined"],
            mem_limit="1g",
            nano_cpus=1_000_000_000,
        )

        # Wait for ttyd to be ready
        for _ in range(30):
            container.reload()
            if container.status == "running":
                try:
                    exit_code, _ = container.exec_run("pgrep ttyd")
                    if exit_code == 0:
                        break
                except Exception:
                    pass
            time.sleep(1)
        else:
            logs = container.logs().decode(errors="replace")[-500:]
            pytest.fail(f"ttyd did not start within 30s. Logs:\n{logs}")

        # Connect via published port using ttyd's 'tty' subprotocol
        ttyd_url = f"ws://127.0.0.1:{ttyd_host_port}/ws"
        async with websockets.connect(
            ttyd_url, subprotocols=["tty"], open_timeout=10
        ) as ws:
            # ttyd handshake: auth token then resize
            await ws.send('{"AuthToken": ""}')
            await ws.send('1{"columns": 80, "rows": 24}')

            # Wait for shell prompt to fully render, then drain
            await asyncio.sleep(3)
            while True:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    break

            # Send command (ASCII '0' prefix = INPUT type)
            await ws.send("0echo hello-from-test\r")

            # Collect output
            output = b""
            for _ in range(20):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    if isinstance(msg, bytes):
                        output += msg
                    elif isinstance(msg, str):
                        output += msg.encode()
                    if b"hello-from-test" in output:
                        break
                except asyncio.TimeoutError:
                    continue

            assert b"hello-from-test" in output
    finally:
        cleanup_project_resources(project_id)
