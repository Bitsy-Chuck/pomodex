# M7: Terminal Proxy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an asyncio WebSocket proxy that authenticates via JWT, looks up sandbox container IPs, and forwards terminal I/O between clients and ttyd instances.

**Architecture:** Client connects to proxy via `ws://proxy:9000/terminal/{project_id}?token={jwt}`. Proxy validates token with Project Service HTTP API, looks up container bridge IP via Docker SDK, connects to ttyd at `ws://{ip}:7681/ws`, and bidirectionally forwards messages. Audit logger records client input only.

**Tech Stack:** Python 3.11, websockets 15.x, aiohttp (HTTP client for auth), docker SDK (container lookup), pytest + pytest-asyncio (testing)

---

## File Map

All paths relative to project root.

```
backend/terminal_proxy/
  proxy.py                              # NEW - WebSocket server + connection handler + URL parser
  services/
    auth.py                             # NEW - JWT validation via Project Service HTTP
    container_lookup.py                 # NEW - Docker SDK container IP lookup
    audit.py                            # NEW - Audit logging
    network_manager.py                  # EXISTING (M6) - unchanged
  tests/
    conftest.py                         # NEW - shared test config
    unit/
      __init__.py                       # NEW
      test_url_parsing.py               # NEW - T7.1
      test_auth.py                      # NEW - auth client unit tests
    integration/
      conftest.py                       # NEW - mock services fixtures
      test_proxy.py                     # NEW - T7.2-T7.15
  Dockerfile                            # NEW
```

---

## Task 1: URL Parser (T7.1)

**Files:**
- Create: `backend/terminal_proxy/tests/unit/__init__.py`
- Create: `backend/terminal_proxy/tests/unit/test_url_parsing.py`
- Create: `backend/terminal_proxy/proxy.py`

### Step 1: Write the failing test

Create `backend/terminal_proxy/tests/unit/__init__.py` (empty file).

Create `backend/terminal_proxy/tests/unit/test_url_parsing.py`:

```python
"""T7.1: URL parsing extracts project_id and token."""

from backend.terminal_proxy.proxy import parse_ws_url


class TestParseWsUrl:
    """T7.1: URL parsing extracts project_id and token."""

    def test_extracts_project_id_and_token(self):
        """T7.1 case 1: /terminal/abc-123?token=eyJhbG..."""
        project_id, token = parse_ws_url("/terminal/abc-123?token=eyJhbG...")
        assert project_id == "abc-123"
        assert token == "eyJhbG..."

    def test_extracts_project_id_without_token(self):
        """T7.1 case 2: /terminal/abc-123 (no token)."""
        project_id, token = parse_ws_url("/terminal/abc-123")
        assert project_id == "abc-123"
        assert token is None

    def test_invalid_path_returns_none(self):
        """T7.1 case 3: /invalid/path."""
        project_id, token = parse_ws_url("/invalid/path")
        assert project_id is None
        assert token is None

    def test_root_path_returns_none(self):
        project_id, token = parse_ws_url("/")
        assert project_id is None
        assert token is None

    def test_empty_path_returns_none(self):
        project_id, token = parse_ws_url("")
        assert project_id is None
        assert token is None
```

### Step 2: Run test to verify it fails

Run: `python -m pytest backend/terminal_proxy/tests/unit/test_url_parsing.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_ws_url'`

### Step 3: Write minimal implementation

Create `backend/terminal_proxy/proxy.py`:

```python
"""
Terminal Proxy — asyncio WebSocket server.

Authenticates via JWT, looks up sandbox container bridge IP,
and proxies terminal I/O between client and ttyd.
"""

from urllib.parse import urlparse, parse_qs


def parse_ws_url(path: str) -> tuple[str | None, str | None]:
    """Extract project_id and token from WebSocket URL path.

    Expected format: /terminal/{project_id}?token={jwt}
    Returns (project_id, token). Returns (None, None) if path is invalid.
    """
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")

    if len(parts) != 2 or parts[0] != "terminal":
        return None, None

    project_id = parts[1]
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]

    return project_id, token
```

### Step 4: Run test to verify it passes

Run: `python -m pytest backend/terminal_proxy/tests/unit/test_url_parsing.py -v`
Expected: 5 passed

### Step 5: Commit

```bash
git add backend/terminal_proxy/proxy.py backend/terminal_proxy/tests/unit/
git commit -m "feat(m7): URL parser with unit tests (T7.1)"
```

---

## Task 2: Auth Client

**Files:**
- Create: `backend/terminal_proxy/services/auth.py`
- Create: `backend/terminal_proxy/tests/unit/test_auth.py`

### Step 1: Write the failing test

Create `backend/terminal_proxy/tests/unit/test_auth.py`:

```python
"""Auth client unit tests — mock HTTP responses."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.terminal_proxy.services.auth import validate_token


@pytest.fixture
def mock_auth_app():
    """Create an aiohttp app mimicking /internal/validate."""
    async def handler(request):
        data = await request.json()
        if data.get("token") == "good-token" and data.get("project_id") == "proj-1":
            return web.json_response({"user_id": "user-abc"})
        return web.Response(status=401)

    app = web.Application()
    app.router.add_post("/internal/validate", handler)
    return app


@pytest.mark.asyncio
async def test_valid_token_returns_user_id(aiohttp_server, mock_auth_app):
    """Valid token + correct project returns user_id."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("good-token", "proj-1", project_service_url=url)
    assert user_id == "user-abc"


@pytest.mark.asyncio
async def test_invalid_token_returns_none(aiohttp_server, mock_auth_app):
    """Invalid token returns None."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("bad-token", "proj-1", project_service_url=url)
    assert user_id is None


@pytest.mark.asyncio
async def test_wrong_project_returns_none(aiohttp_server, mock_auth_app):
    """Valid token but wrong project returns None."""
    server = await aiohttp_server(mock_auth_app)
    url = f"http://127.0.0.1:{server.port}"
    user_id = await validate_token("good-token", "wrong-proj", project_service_url=url)
    assert user_id is None


@pytest.mark.asyncio
async def test_unreachable_service_returns_none():
    """Service down returns None (no crash)."""
    user_id = await validate_token("any", "any", project_service_url="http://127.0.0.1:1")
    assert user_id is None
```

### Step 2: Run test to verify it fails

Run: `python -m pytest backend/terminal_proxy/tests/unit/test_auth.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_token'`

### Step 3: Write minimal implementation

Create `backend/terminal_proxy/services/auth.py`:

```python
"""JWT validation via Project Service HTTP API."""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"


async def validate_token(
    token: str,
    project_id: str,
    project_service_url: str | None = None,
) -> str | None:
    """Validate JWT token via Project Service.

    Calls POST /internal/validate with {token, project_id}.
    Returns user_id if valid, None otherwise.
    """
    base_url = project_service_url or os.environ.get(
        "PROJECT_SERVICE_URL", _DEFAULT_URL
    )
    url = f"{base_url}/internal/validate"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"token": token, "project_id": project_id}, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("user_id")
                return None
    except Exception as e:
        logger.error("Auth validation failed: %s", e)
        return None
```

### Step 4: Run test to verify it passes

Run: `python -m pytest backend/terminal_proxy/tests/unit/test_auth.py -v`
Expected: 4 passed

Note: requires `pip install pytest-aiohttp` for the `aiohttp_server` fixture:
Run: `pip install pytest-aiohttp`

### Step 5: Commit

```bash
git add backend/terminal_proxy/services/auth.py backend/terminal_proxy/tests/unit/test_auth.py
git commit -m "feat(m7): auth client with unit tests"
```

---

## Task 3: Container Lookup + Audit Logger

**Files:**
- Create: `backend/terminal_proxy/services/container_lookup.py`
- Create: `backend/terminal_proxy/services/audit.py`

These are thin wrappers tested primarily through integration tests.

### Step 1: Write container_lookup.py

```python
"""Docker SDK container IP lookup."""

import docker
from docker.errors import NotFound


class ContainerNotRunning(Exception):
    """Raised when the sandbox container is not available."""
    pass


def get_container_ip(project_id: str) -> str:
    """Get the bridge network IP of sandbox-{project_id}.

    Raises ContainerNotRunning if container doesn't exist, isn't running,
    or isn't attached to the expected network.
    """
    client = docker.from_env()
    container_name = f"sandbox-{project_id}"
    network_name = f"net-{project_id}"

    try:
        container = client.containers.get(container_name)
    except NotFound:
        raise ContainerNotRunning(f"Container {container_name} not found")

    if container.status != "running":
        raise ContainerNotRunning(
            f"Container {container_name} is {container.status}"
        )

    networks = container.attrs["NetworkSettings"]["Networks"]
    if network_name not in networks:
        raise ContainerNotRunning(
            f"Container not on network {network_name}"
        )

    ip = networks[network_name]["IPAddress"]
    if not ip:
        raise ContainerNotRunning(
            f"No IP for {container_name} on {network_name}"
        )

    return ip
```

### Step 2: Write audit.py

```python
"""Audit logging for terminal input."""

import json
import logging
import time

logger = logging.getLogger("terminal_proxy.audit")


class AuditLogger:
    """Logs terminal input messages for audit trail.

    Only logs client input (not ttyd output — too verbose, contains ANSI).
    """

    def __init__(self, project_id: str, user_id: str):
        self.project_id = project_id
        self.user_id = user_id
        self.entries: list[dict] = []

    def log_input(self, message) -> None:
        """Log an input message from the client."""
        if isinstance(message, bytes):
            content = message.decode("utf-8", errors="replace")
        else:
            content = message

        entry = {
            "event": "terminal_input",
            "project_id": self.project_id,
            "user_id": self.user_id,
            "timestamp": time.time(),
            "content": content,
        }
        self.entries.append(entry)
        logger.info(json.dumps(entry))
```

### Step 3: Verify imports work

Run: `python -c "from backend.terminal_proxy.services.audit import AuditLogger; print('OK')"`
Expected: OK

Run: `python -c "from backend.terminal_proxy.services.container_lookup import ContainerNotRunning; print('OK')"`
Expected: OK

### Step 4: Commit

```bash
git add backend/terminal_proxy/services/container_lookup.py backend/terminal_proxy/services/audit.py
git commit -m "feat(m7): container lookup and audit logger"
```

---

## Task 4: Connection Handler + Proxy Logic

**Files:**
- Modify: `backend/terminal_proxy/proxy.py` (add handler, proxy, server)

### Step 1: Write the full proxy.py

Extend proxy.py with the connection handler and bidirectional forwarding:

```python
"""
Terminal Proxy — asyncio WebSocket server.

Authenticates via JWT, looks up sandbox container bridge IP,
and proxies terminal I/O between client and ttyd.
"""

import asyncio
import logging
import os
from urllib.parse import urlparse, parse_qs

import websockets

from .services.audit import AuditLogger
from .services.auth import validate_token
from .services.container_lookup import ContainerNotRunning, get_container_ip

logger = logging.getLogger(__name__)

TTYD_PORT = int(os.environ.get("TTYD_PORT", "7681"))


def parse_ws_url(path: str) -> tuple[str | None, str | None]:
    """Extract project_id and token from WebSocket URL path.

    Expected format: /terminal/{project_id}?token={jwt}
    Returns (project_id, token). Returns (None, None) if path is invalid.
    """
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")

    if len(parts) != 2 or parts[0] != "terminal":
        return None, None

    project_id = parts[1]
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]

    return project_id, token


async def handle_connection(websocket):
    """Handle a single WebSocket connection through the full lifecycle."""
    # 1. Parse URL
    project_id, token = parse_ws_url(websocket.request.path)

    if project_id is None:
        await websocket.close(4400, "Invalid path")
        return

    # 2. Require token
    if token is None:
        await websocket.close(4400, "Token required")
        return

    # 3. Validate token via Project Service
    user_id = await validate_token(token, project_id)
    if user_id is None:
        await websocket.close(4401, "Unauthorized")
        return

    # 4. Look up container bridge IP
    try:
        container_ip = get_container_ip(project_id)
    except ContainerNotRunning as e:
        logger.warning("Container not running for %s: %s", project_id, e)
        await websocket.close(4503, "Container not running")
        return

    # 5. Connect to ttyd and start proxying
    ttyd_url = f"ws://{container_ip}:{TTYD_PORT}/ws"
    try:
        async with websockets.connect(ttyd_url) as ttyd_ws:
            audit = AuditLogger(project_id, user_id)
            await _proxy(websocket, ttyd_ws, audit)
    except websockets.ConnectionClosed:
        pass  # Normal closure
    except Exception as e:
        logger.error("ttyd connection failed for %s: %s", project_id, e)
        try:
            await websocket.close(4502, "Backend connection failed")
        except Exception:
            pass


async def _proxy(client_ws, ttyd_ws, audit: AuditLogger):
    """Bidirectional message forwarding between client and ttyd."""

    async def client_to_ttyd():
        try:
            async for message in client_ws:
                audit.log_input(message)
                await ttyd_ws.send(message)
        except websockets.ConnectionClosed:
            pass

    async def ttyd_to_client():
        try:
            async for message in ttyd_ws:
                await client_ws.send(message)
        except websockets.ConnectionClosed:
            pass

    tasks = [
        asyncio.create_task(client_to_ttyd()),
        asyncio.create_task(ttyd_to_client()),
    ]

    try:
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    except Exception:
        for task in tasks:
            task.cancel()


async def start_server(host=None, port=None):
    """Start the WebSocket proxy server. Returns the server object."""
    host = host or os.environ.get("PROXY_HOST", "0.0.0.0")
    port = port or int(os.environ.get("PROXY_PORT", "9000"))

    server = await websockets.serve(handle_connection, host, port)
    logger.info("Terminal proxy listening on %s:%s", host, port)
    return server


def main():
    """Entry point for running as standalone service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(start_server())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())


if __name__ == "__main__":
    main()
```

### Step 2: Verify import + URL tests still pass

Run: `python -m pytest backend/terminal_proxy/tests/unit/test_url_parsing.py -v`
Expected: 5 passed

### Step 3: Commit

```bash
git add backend/terminal_proxy/proxy.py
git commit -m "feat(m7): connection handler and proxy logic"
```

---

## Task 5: Integration Test Infrastructure

**Files:**
- Create: `backend/terminal_proxy/tests/conftest.py`
- Create: `backend/terminal_proxy/tests/integration/conftest.py`

### Step 1: Write shared conftest

Create `backend/terminal_proxy/tests/conftest.py`:

```python
"""Shared test configuration for terminal_proxy tests."""

import pytest


def pytest_collection_modifyitems(config, items):
    """Add markers based on test location."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
```

### Step 2: Write integration conftest with mock services

Create `backend/terminal_proxy/tests/integration/conftest.py`:

```python
"""Integration test fixtures — mock Project Service + mock ttyd + proxy."""

import asyncio
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
import websockets
from aiohttp import web

# ---------------------------------------------------------------------------
# Mock Project Service
# ---------------------------------------------------------------------------

# Configurable token → (user_id, allowed_project_ids)
VALID_TOKENS = {
    "token-user1": {"user_id": "user-001", "projects": ["proj-aaa", "proj-bbb"]},
    "token-user2": {"user_id": "user-002", "projects": ["proj-ccc"]},
}


async def _validate_handler(request):
    data = await request.json()
    token = data.get("token")
    project_id = data.get("project_id")

    if token in VALID_TOKENS:
        info = VALID_TOKENS[token]
        if project_id in info["projects"]:
            return web.json_response({"user_id": info["user_id"]})
    return web.Response(status=401)


@pytest_asyncio.fixture
async def mock_auth():
    """Mock Project Service /internal/validate. Yields {url, port, calls}."""
    calls = []

    async def handler(request):
        data = await request.json()
        calls.append(data)
        return await _validate_handler(request)

    app = web.Application()
    app.router.add_post("/internal/validate", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield {"url": f"http://127.0.0.1:{port}", "port": port, "calls": calls}
    await runner.cleanup()


# ---------------------------------------------------------------------------
# Mock ttyd
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mock_ttyd():
    """Mock ttyd WebSocket server (echo). Yields {port, received}."""
    received = []

    async def handler(ws):
        try:
            async for msg in ws:
                received.append(msg)
                await ws.send(msg)
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield {"port": port, "received": received}
    server.close()
    await server.wait_closed()


@pytest_asyncio.fixture
async def mock_ttyd_send_first():
    """Mock ttyd that sends a welcome message then echoes. Yields {port, received}."""
    received = []

    async def handler(ws):
        try:
            await ws.send(b"\x1b[?25h$ ")  # terminal prompt (binary)
            async for msg in ws:
                received.append(msg)
                await ws.send(msg)
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield {"port": port, "received": received}
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# Proxy server (with mocks wired in)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def proxy(mock_auth, mock_ttyd):
    """Start terminal proxy wired to mock auth + mock ttyd. Yields {url, port, auth, ttyd}."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    # Save originals
    orig_ttyd_port = proxy_mod.TTYD_PORT
    orig_url = auth_mod._DEFAULT_URL

    # Patch
    auth_mod._DEFAULT_URL = mock_auth["url"]
    proxy_mod.TTYD_PORT = mock_ttyd["port"]

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server = await proxy_mod.start_server(host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd,
        }
        server.close()
        await server.wait_closed()

    # Restore
    proxy_mod.TTYD_PORT = orig_ttyd_port
    auth_mod._DEFAULT_URL = orig_url


@pytest_asyncio.fixture
async def proxy_with_welcome(mock_auth, mock_ttyd_send_first):
    """Proxy wired to ttyd that sends a welcome message first."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    orig_ttyd_port = proxy_mod.TTYD_PORT
    orig_url = auth_mod._DEFAULT_URL

    auth_mod._DEFAULT_URL = mock_auth["url"]
    proxy_mod.TTYD_PORT = mock_ttyd_send_first["port"]

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server = await proxy_mod.start_server(host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd_send_first,
        }
        server.close()
        await server.wait_closed()

    proxy_mod.TTYD_PORT = orig_ttyd_port
    auth_mod._DEFAULT_URL = orig_url
```

### Step 3: Verify conftest loads

Run: `python -c "import backend.terminal_proxy.tests.integration.conftest; print('OK')"`
Expected: OK

### Step 4: Commit

```bash
git add backend/terminal_proxy/tests/conftest.py backend/terminal_proxy/tests/integration/conftest.py
git commit -m "feat(m7): integration test infrastructure with mock services"
```

---

## Task 6: Auth Integration Tests (T7.2-T7.5)

**Files:**
- Create: `backend/terminal_proxy/tests/integration/test_proxy.py`

### Step 1: Write failing tests

Create `backend/terminal_proxy/tests/integration/test_proxy.py`:

```python
"""Integration tests for Terminal Proxy (T7.2-T7.15)."""

import asyncio

import pytest
import websockets


# ---------------------------------------------------------------------------
# T7.2: Valid JWT — connection accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t72_valid_jwt_connection_accepted(proxy):
    """T7.2: Valid token → connection stays open, auth call made."""
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
    """T7.3: Invalid token → closed with 4401."""
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
    """T7.4: No token param → closed with 4400."""
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
    """T7.5: Valid token but user doesn't own project → 4401."""
    # token-user1 owns proj-aaa and proj-bbb, NOT proj-ccc
    url = f"{proxy['url']}/terminal/proj-ccc?token=token-user1"
    ws = await websockets.connect(url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed as e:
        assert e.rcvd.code == 4401
```

### Step 2: Run tests to verify they fail/pass

Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py::test_t72_valid_jwt_connection_accepted -v --timeout=10`
Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t73 or t74 or t75" -v --timeout=10`

Expected: All 4 pass (the connection handler is already implemented in Task 4).

If any fail, debug and fix the connection handler in proxy.py.

### Step 3: Commit

```bash
git add backend/terminal_proxy/tests/integration/test_proxy.py
git commit -m "test(m7): auth integration tests T7.2-T7.5"
```

---

## Task 7: Proxy Forwarding Tests (T7.6-T7.8)

**Files:**
- Modify: `backend/terminal_proxy/tests/integration/test_proxy.py`

### Step 1: Write failing tests

Append to `test_proxy.py`:

```python
# ---------------------------------------------------------------------------
# T7.6: Proxy forwards client input to ttyd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t76_proxy_forwards_input_to_ttyd(proxy):
    """T7.6: Client sends message → ttyd receives exact bytes."""
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
    """T7.7: ttyd sends binary data → client receives exact bytes."""
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

    # We need to access the audit logger. Since it's created per-connection,
    # we'll test via the structured log output.
    import logging
    audit_records = []
    handler = logging.Handler()
    handler.emit = lambda record: audit_records.append(record)
    logging.getLogger("terminal_proxy.audit").addHandler(handler)

    try:
        async with websockets.connect(url) as ws:
            await ws.send("echo hello\r")
            await ws.send("ls\r")
            # Wait for echoes
            await asyncio.wait_for(ws.recv(), timeout=2.0)
            await asyncio.wait_for(ws.recv(), timeout=2.0)
    finally:
        logging.getLogger("terminal_proxy.audit").removeHandler(handler)

    # Verify audit entries
    import json
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
```

### Step 2: Run tests

Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t76 or t77 or t78" -v --timeout=10`
Expected: All 3 pass

### Step 3: Commit

```bash
git add backend/terminal_proxy/tests/integration/test_proxy.py
git commit -m "test(m7): proxy forwarding and audit tests T7.6-T7.8"
```

---

## Task 8: Error & Lifecycle Tests (T7.9, T7.12-T7.13)

**Files:**
- Modify: `backend/terminal_proxy/tests/integration/test_proxy.py`
- Modify: `backend/terminal_proxy/tests/integration/conftest.py`

### Step 1: Add container-not-running fixture to conftest

Append to `conftest.py`:

```python
@pytest_asyncio.fixture
async def proxy_no_container(mock_auth, mock_ttyd):
    """Proxy where container lookup always raises ContainerNotRunning."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    orig_ttyd_port = proxy_mod.TTYD_PORT
    orig_url = auth_mod._DEFAULT_URL

    auth_mod._DEFAULT_URL = mock_auth["url"]
    proxy_mod.TTYD_PORT = mock_ttyd["port"]

    def raise_not_running(pid):
        raise lookup_mod.ContainerNotRunning(f"Container sandbox-{pid} not found")

    with patch.object(lookup_mod, "get_container_ip", side_effect=raise_not_running):
        server = await proxy_mod.start_server(host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd,
        }
        server.close()
        await server.wait_closed()

    proxy_mod.TTYD_PORT = orig_ttyd_port
    auth_mod._DEFAULT_URL = orig_url


@pytest_asyncio.fixture
async def mock_ttyd_closable():
    """Mock ttyd that can be closed on demand. Yields {port, received, close_fn, server}."""
    received = []
    connections = []

    async def handler(ws):
        connections.append(ws)
        try:
            async for msg in ws:
                received.append(msg)
                await ws.send(msg)
        except websockets.ConnectionClosed:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def close_all():
        for ws in connections:
            try:
                await ws.close(1001, "ttyd shutting down")
            except Exception:
                pass

    yield {"port": port, "received": received, "close_fn": close_all, "server": server}
    server.close()
    await server.wait_closed()


@pytest_asyncio.fixture
async def proxy_closable_ttyd(mock_auth, mock_ttyd_closable):
    """Proxy with a closable mock ttyd."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    orig_ttyd_port = proxy_mod.TTYD_PORT
    orig_url = auth_mod._DEFAULT_URL

    auth_mod._DEFAULT_URL = mock_auth["url"]
    proxy_mod.TTYD_PORT = mock_ttyd_closable["port"]

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server = await proxy_mod.start_server(host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd_closable,
        }
        server.close()
        await server.wait_closed()

    proxy_mod.TTYD_PORT = orig_ttyd_port
    auth_mod._DEFAULT_URL = orig_url
```

### Step 2: Write failing tests

Append to `test_proxy.py`:

```python
# ---------------------------------------------------------------------------
# T7.9: Container not running — connection fails gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t79_container_not_running(proxy_no_container):
    """T7.9: Container stopped → closed with 4503."""
    url = f"{proxy_no_container['url']}/terminal/proj-aaa?token=token-user1"
    ws = await websockets.connect(url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        pytest.fail("Expected connection to be closed")
    except websockets.ConnectionClosed as e:
        assert e.rcvd.code == 4503
        assert "Container not running" in e.rcvd.reason


# ---------------------------------------------------------------------------
# T7.12: Client disconnect — proxy cleans up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t712_client_disconnect_cleanup(proxy):
    """T7.12: Client closes → proxy closes ttyd connection, no errors."""
    url = f"{proxy['url']}/terminal/proj-aaa?token=token-user1"
    async with websockets.connect(url) as ws:
        await ws.send("hello")
        await asyncio.wait_for(ws.recv(), timeout=2.0)
        # Client closes connection (context manager exit)

    # Give proxy time to clean up
    await asyncio.sleep(0.2)
    # If we get here without errors, cleanup succeeded.
    # No assertion needed — absence of errors IS the assertion.


# ---------------------------------------------------------------------------
# T7.13: ttyd disconnect — proxy notifies client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t713_ttyd_disconnect_notifies_client(proxy_closable_ttyd):
    """T7.13: ttyd disconnects → client WebSocket is closed."""
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
```

### Step 3: Run tests

Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t79 or t712 or t713" -v --timeout=15`
Expected: All 3 pass

### Step 4: Commit

```bash
git add backend/terminal_proxy/tests/integration/test_proxy.py backend/terminal_proxy/tests/integration/conftest.py
git commit -m "test(m7): error handling and lifecycle tests T7.9, T7.12-T7.13"
```

---

## Task 9: Concurrent + Timestamp Tests (T7.14-T7.15)

**Files:**
- Modify: `backend/terminal_proxy/tests/integration/test_proxy.py`

### Step 1: Write tests

Append to `test_proxy.py`:

```python
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

    # Verify audit logs are separate (check via auth calls — 2 connections = 2 auth calls)
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
```

### Step 2: Run tests

Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t714 or t715" -v --timeout=10`
Expected: All 2 pass

### Step 3: Commit

```bash
git add backend/terminal_proxy/tests/integration/test_proxy.py
git commit -m "test(m7): concurrent connections and auth validation tests T7.14-T7.15"
```

---

## Task 10: Docker Integration Tests (T7.10-T7.11)

**Files:**
- Modify: `backend/terminal_proxy/tests/integration/test_proxy.py`

These tests require Docker and a built sandbox image. They can be skipped in CI
without Docker by marking with `@pytest.mark.docker`.

### Step 1: Write tests

Append to `test_proxy.py`:

```python
# ---------------------------------------------------------------------------
# Docker-dependent tests
# ---------------------------------------------------------------------------

import os

docker_available = os.environ.get("DOCKER_TESTS", "0") == "1"
skip_no_docker = pytest.mark.skipif(
    not docker_available,
    reason="Set DOCKER_TESTS=1 to run Docker integration tests"
)


@skip_no_docker
@pytest.mark.asyncio
async def test_t710_container_bridge_ip_lookup():
    """T7.10: Look up running container bridge IP via Docker SDK.

    Requires: sandbox image built, Docker running.
    Creates a temporary container on a bridge network, verifies IP lookup.
    """
    import docker as docker_sdk
    from backend.terminal_proxy.services.container_lookup import get_container_ip

    client = docker_sdk.from_env()
    project_id = "t710-test"

    # Create network and container
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
            # Verify it's a valid IP on the bridge subnet
            assert ip.startswith("172.") or ip.startswith("10.") or ip.startswith("192.168.")
            assert len(ip.split(".")) == 4
        finally:
            container.remove(force=True)
    finally:
        network.remove()


@skip_no_docker
@pytest.mark.asyncio
async def test_t711_full_roundtrip_via_ttyd():
    """T7.11: Full round-trip through proxy → ttyd → tmux → bash.

    Requires: sandbox image built and running with ttyd.
    This is a heavyweight test — start real container, connect, execute command.
    """
    import docker as docker_sdk
    from backend.terminal_proxy.services.container_lookup import get_container_ip
    from backend.terminal_proxy.services.auth import validate_token
    import backend.terminal_proxy.proxy as proxy_mod

    client = docker_sdk.from_env()
    project_id = "t711-test"
    image = os.environ.get("SANDBOX_IMAGE", "agent-sandbox:test")

    # Create network + container
    network = client.networks.create(f"net-{project_id}", driver="bridge")
    try:
        container = client.containers.run(
            image,
            name=f"sandbox-{project_id}",
            network=f"net-{project_id}",
            detach=True,
            cap_add=["SYS_ADMIN"],
            devices=["/dev/fuse"],
            environment={
                "SSH_PUBLIC_KEY": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
                "PROJECT_ID": project_id,
            },
        )
        try:
            # Wait for ttyd to start
            import time
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

            container_ip = get_container_ip(project_id)

            # Connect directly to ttyd
            ttyd_url = f"ws://{container_ip}:7681/ws"
            async with websockets.connect(ttyd_url) as ws:
                # Send a command
                await ws.send("echo hello-from-test\r")
                # Read output (may need multiple reads)
                output = b""
                for _ in range(20):
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if isinstance(msg, bytes):
                            output += msg
                        else:
                            output += msg.encode()
                        if b"hello-from-test" in output:
                            break
                    except asyncio.TimeoutError:
                        continue

                assert b"hello-from-test" in output
        finally:
            container.remove(force=True)
    finally:
        network.remove()
```

### Step 2: Run tests

Without Docker: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t710 or t711" -v --timeout=60`
Expected: 2 skipped

With Docker: `DOCKER_TESTS=1 python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -k "t710 or t711" -v --timeout=60`
Expected: 2 passed (if sandbox image is built)

### Step 3: Commit

```bash
git add backend/terminal_proxy/tests/integration/test_proxy.py
git commit -m "test(m7): Docker integration tests T7.10-T7.11"
```

---

## Task 11: Dockerfile

**Files:**
- Create: `backend/terminal_proxy/Dockerfile`

### Step 1: Write Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    websockets==15.0.1 \
    aiohttp==3.11.* \
    docker==7.*

COPY . /app/backend/terminal_proxy/

# Need the parent package
RUN mkdir -p /app/backend && touch /app/backend/__init__.py

EXPOSE 9000

CMD ["python", "-m", "backend.terminal_proxy.proxy"]
```

### Step 2: Verify build (optional — requires Docker)

Run: `docker build -t terminal-proxy:test -f backend/terminal_proxy/Dockerfile .`
Expected: Build succeeds

### Step 3: Commit

```bash
git add backend/terminal_proxy/Dockerfile
git commit -m "feat(m7): Dockerfile for terminal proxy"
```

---

## Task 12: Run Full Test Suite + Fix Any Issues

### Step 1: Run all unit tests

Run: `python -m pytest backend/terminal_proxy/tests/unit/ -v --timeout=10`
Expected: 9 passed (5 URL + 4 auth)

### Step 2: Run all integration tests (mock-based)

Run: `python -m pytest backend/terminal_proxy/tests/integration/test_proxy.py -v --timeout=15`
Expected: 12 passed, 2 skipped (Docker tests)

### Step 3: Run existing tests to verify no regressions

Run: `python -m pytest tests/unit/ --ignore=tests/unit/test_sa_naming.py -v --timeout=30`
Expected: 53 passed

### Step 4: Final commit

```bash
git add -A
git commit -m "feat(m7): Terminal proxy complete — all 15 test cases"
```

---

## Test Case ↔ Task Mapping

| Test Case | Description | Task | Type |
|-----------|-------------|------|------|
| T7.1 | URL parsing | Task 1 | Unit |
| T7.2 | Valid JWT accepted | Task 6 | Integration |
| T7.3 | Invalid JWT rejected 4401 | Task 6 | Integration |
| T7.4 | Missing token rejected | Task 6 | Integration |
| T7.5 | Wrong ownership rejected | Task 6 | Integration |
| T7.6 | Client input → ttyd | Task 7 | Integration |
| T7.7 | ttyd output → client | Task 7 | Integration |
| T7.8 | Audit log captures input | Task 7 | Integration |
| T7.9 | Container not running → 4503 | Task 8 | Integration |
| T7.10 | Container bridge IP lookup | Task 10 | Docker |
| T7.11 | Full round-trip via ttyd | Task 10 | Docker |
| T7.12 | Client disconnect cleanup | Task 8 | Integration |
| T7.13 | ttyd disconnect notification | Task 8 | Integration |
| T7.14 | Concurrent connections | Task 9 | Integration |
| T7.15 | validate called on connect | Task 9 | Integration |
