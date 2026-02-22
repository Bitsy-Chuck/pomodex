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
from .services import container_lookup

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
    logger.info("[WS] new connection from %s path=%s", websocket.remote_address, websocket.request.path)

    # 1. Parse URL
    project_id, token = parse_ws_url(websocket.request.path)

    if project_id is None:
        logger.info("[WS] rejected: invalid path")
        await websocket.close(4400, "Invalid path")
        return

    # 2. Require token
    if token is None:
        logger.info("[WS] rejected: no token for project=%s", project_id)
        await websocket.close(4400, "Token required")
        return

    logger.info("[WS] project=%s token_len=%d token_prefix=%s", project_id, len(token), token[:20])

    # 3. Validate token via Project Service
    user_id = await validate_token(token, project_id)
    if user_id is None:
        logger.info("[WS] rejected: validate_token returned None for project=%s", project_id)
        await websocket.close(4401, "Unauthorized")
        return

    logger.info("[WS] authenticated user=%s project=%s", user_id, project_id)

    # 4. Look up container bridge IP
    try:
        container_ip = container_lookup.get_container_ip(project_id)
    except container_lookup.ContainerNotRunning as e:
        logger.warning("[WS] container not running for %s: %s", project_id, e)
        await websocket.close(4503, "Container not running")
        return

    # 5. Connect to ttyd and start proxying
    ttyd_url = f"ws://{container_ip}:{TTYD_PORT}/ws"
    logger.info("[WS] connecting to ttyd at %s", ttyd_url)
    try:
        async with websockets.connect(ttyd_url, subprotocols=["tty"]) as ttyd_ws:
            logger.info("[WS] ttyd connected, proxying for user=%s project=%s", user_id, project_id)
            logger.info("[PROXY] starting bidirectional relay")
            audit = AuditLogger(project_id, user_id)
            await _proxy(websocket, ttyd_ws, audit)
            logger.info("[PROXY] relay ended")
    except websockets.ConnectionClosed:
        logger.info("[WS] connection closed for project=%s", project_id)
    except Exception as e:
        logger.error("[WS] ttyd connection failed for %s: %s", project_id, e)
        try:
            await websocket.close(4502, "Backend connection failed")
        except Exception:
            pass


async def _proxy(client_ws, ttyd_ws, audit: AuditLogger):
    """Bidirectional message forwarding between client and ttyd."""

    async def client_to_ttyd():
        try:
            async for message in client_ws:
                logger.info("[PROXY] client→ttyd type=%s len=%d", type(message).__name__, len(message))
                audit.log_input(message)
                await ttyd_ws.send(message)
        except websockets.ConnectionClosed:
            pass

    async def ttyd_to_client():
        try:
            async for message in ttyd_ws:
                logger.info("[PROXY] ttyd→client type=%s len=%d preview=%s",
                            type(message).__name__, len(message),
                            repr(message[:80]) if len(message) <= 200 else repr(message[:80]) + "...")
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
