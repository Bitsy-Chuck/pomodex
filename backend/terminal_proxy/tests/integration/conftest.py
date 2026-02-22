"""Integration test fixtures — mock Project Service + mock ttyd + proxy."""

from unittest.mock import patch

import pytest_asyncio
import websockets
from aiohttp import web

# ---------------------------------------------------------------------------
# Mock Project Service
# ---------------------------------------------------------------------------

# Configurable token -> (user_id, allowed_project_ids)
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
# Mock ttyd variants
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
    """Mock ttyd that sends a welcome message then echoes."""
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


@pytest_asyncio.fixture
async def mock_ttyd_closable():
    """Mock ttyd that can be closed on demand."""
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

    yield {"port": port, "received": received, "close_fn": close_all}
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# Helper to start proxy with patched services
# ---------------------------------------------------------------------------


async def _start_proxy(mock_auth_data, ttyd_port, lookup_patch):
    """Start proxy server wired to mock services. Returns (server, port, yield_data)."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod

    orig_ttyd_port = proxy_mod.TTYD_PORT
    orig_url = auth_mod._DEFAULT_URL

    auth_mod._DEFAULT_URL = mock_auth_data["url"]
    proxy_mod.TTYD_PORT = ttyd_port

    server = await proxy_mod.start_server(host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]

    return server, port, orig_ttyd_port, orig_url


async def _stop_proxy(server, orig_ttyd_port, orig_url):
    """Stop proxy and restore patched values."""
    import backend.terminal_proxy.proxy as proxy_mod
    import backend.terminal_proxy.services.auth as auth_mod

    server.close()
    await server.wait_closed()
    proxy_mod.TTYD_PORT = orig_ttyd_port
    auth_mod._DEFAULT_URL = orig_url


# ---------------------------------------------------------------------------
# Proxy fixtures (explicit deps, no factory)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def proxy(mock_auth, mock_ttyd):
    """Proxy wired to echo ttyd."""
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server, port, orig_ttyd, orig_url = await _start_proxy(
            mock_auth, mock_ttyd["port"], None
        )
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd,
        }
        await _stop_proxy(server, orig_ttyd, orig_url)


@pytest_asyncio.fixture
async def proxy_with_welcome(mock_auth, mock_ttyd_send_first):
    """Proxy wired to ttyd that sends a welcome message first."""
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server, port, orig_ttyd, orig_url = await _start_proxy(
            mock_auth, mock_ttyd_send_first["port"], None
        )
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd_send_first,
        }
        await _stop_proxy(server, orig_ttyd, orig_url)


@pytest_asyncio.fixture
async def proxy_closable_ttyd(mock_auth, mock_ttyd_closable):
    """Proxy with a closable mock ttyd."""
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    with patch.object(lookup_mod, "get_container_ip", return_value="127.0.0.1"):
        server, port, orig_ttyd, orig_url = await _start_proxy(
            mock_auth, mock_ttyd_closable["port"], None
        )
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd_closable,
        }
        await _stop_proxy(server, orig_ttyd, orig_url)


@pytest_asyncio.fixture
async def proxy_no_container(mock_auth, mock_ttyd):
    """Proxy where container lookup always raises ContainerNotRunning."""
    import backend.terminal_proxy.services.container_lookup as lookup_mod

    def raise_not_running(pid):
        raise lookup_mod.ContainerNotRunning(f"Container sandbox-{pid} not found")

    with patch.object(lookup_mod, "get_container_ip", side_effect=raise_not_running):
        server, port, orig_ttyd, orig_url = await _start_proxy(
            mock_auth, mock_ttyd["port"], None
        )
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "auth": mock_auth,
            "ttyd": mock_ttyd,
        }
        await _stop_proxy(server, orig_ttyd, orig_url)
