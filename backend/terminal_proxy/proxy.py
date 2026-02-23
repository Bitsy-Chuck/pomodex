"""
Terminal Proxy — asyncio WebSocket server.

Authenticates via JWT, looks up sandbox container bridge IP,
and proxies terminal I/O between client and ttyd.
"""

import asyncio
import logging
import os
import time
from urllib.parse import urlparse, parse_qs

import websockets

from .services.audit import AuditLogger
from .services.auth import validate_token
from .services import container_lookup

logger = logging.getLogger(__name__)

TTYD_PORT = int(os.environ.get("TTYD_PORT", "7681"))

# Monotonic connection counter for correlating log lines
_conn_seq = 0


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
    global _conn_seq
    _conn_seq += 1
    cid = _conn_seq
    t0 = time.monotonic()

    remote = websocket.remote_address
    logger.info("[conn:%d] NEW connection from %s path=%s", cid, remote, websocket.request.path)

    project_id, token = parse_ws_url(websocket.request.path)

    if project_id is None:
        logger.warning("[conn:%d] rejected: invalid path", cid)
        await websocket.close(4400, "Invalid path")
        return

    if token is None:
        logger.warning("[conn:%d] rejected: no token", cid)
        await websocket.close(4400, "Token required")
        return

    tag = f"conn:{cid}|{project_id[:8]}"

    logger.info("[%s] authenticating (token_len=%d)", tag, len(token))
    user_id = await validate_token(token, project_id)
    if user_id is None:
        logger.warning("[%s] rejected: auth failed (+%.3fs)", tag, time.monotonic() - t0)
        await websocket.close(4401, "Unauthorized")
        return

    logger.info("[%s] authenticated user=%s (+%.3fs)", tag, user_id, time.monotonic() - t0)

    try:
        container_ip = container_lookup.get_container_ip(project_id)
    except container_lookup.ContainerNotRunning as e:
        logger.warning("[%s] container not running: %s (+%.3fs)", tag, e, time.monotonic() - t0)
        await websocket.close(4503, "Container not running")
        return

    ttyd_url = f"ws://{container_ip}:{TTYD_PORT}/ws"
    logger.info("[%s] connecting to ttyd at %s (+%.3fs)", tag, ttyd_url, time.monotonic() - t0)

    try:
        async with websockets.connect(ttyd_url, subprotocols=["tty"], open_timeout=5) as ttyd_ws:
            logger.info("[%s] ttyd connected (subprotocol=%s), starting relay (+%.3fs)",
                        tag, ttyd_ws.subprotocol, time.monotonic() - t0)
            audit = AuditLogger(project_id, user_id)
            close_reason = await _proxy(websocket, ttyd_ws, audit, tag)
            elapsed = time.monotonic() - t0
            logger.info("[%s] relay ended: %s (duration=%.3fs)", tag, close_reason, elapsed)
    except websockets.ConnectionClosed as e:
        logger.info("[%s] connection closed during setup: code=%s reason=%s (+%.3fs)",
                    tag, e.code, e.reason, time.monotonic() - t0)
    except Exception as e:
        logger.error("[%s] ttyd connection failed: %s: %s (+%.3fs)",
                     tag, type(e).__name__, e, time.monotonic() - t0)
        try:
            await websocket.close(4502, "Backend connection failed")
        except Exception:
            pass

    logger.info("[%s] handle_connection exiting (+%.3fs)", tag, time.monotonic() - t0)


async def _proxy(client_ws, ttyd_ws, audit: AuditLogger, tag: str) -> str:
    """Bidirectional message forwarding between client and ttyd.

    Returns a string describing why the relay ended.
    """
    client_msgs = 0
    ttyd_msgs = 0
    close_reason = "unknown"

    async def client_to_ttyd():
        nonlocal client_msgs, close_reason
        try:
            async for message in client_ws:
                client_msgs += 1
                mtype = "bytes" if isinstance(message, bytes) else "str"
                if client_msgs <= 3:
                    logger.info("[%s] client→ttyd msg#%d (%s, %d bytes)",
                                tag, client_msgs, mtype, len(message))
                audit.log_input(message)
                await ttyd_ws.send(message)
        except websockets.ConnectionClosed as e:
            close_reason = f"client→ttyd ConnectionClosed code={e.code} reason={e.reason!r}"
            logger.info("[%s] %s (after %d msgs)", tag, close_reason, client_msgs)
            return
        # async for ended normally — client sent close frame
        close_reason = f"client closed cleanly (close_code={client_ws.close_code} close_reason={client_ws.close_reason!r})"
        logger.info("[%s] client→ttyd loop ended: %s (after %d msgs)", tag, close_reason, client_msgs)

    async def ttyd_to_client():
        nonlocal ttyd_msgs, close_reason
        try:
            async for message in ttyd_ws:
                ttyd_msgs += 1
                if ttyd_msgs <= 3:
                    mtype = "bytes" if isinstance(message, bytes) else "str"
                    logger.info("[%s] ttyd→client msg#%d (%s, %d bytes)",
                                tag, ttyd_msgs, mtype, len(message))
                await client_ws.send(message)
        except websockets.ConnectionClosed as e:
            close_reason = f"ttyd→client ConnectionClosed code={e.code} reason={e.reason!r}"
            logger.info("[%s] %s (after %d msgs)", tag, close_reason, ttyd_msgs)
            return
        # async for ended normally — ttyd sent close frame
        close_reason = f"ttyd closed cleanly (close_code={ttyd_ws.close_code} close_reason={ttyd_ws.close_reason!r})"
        logger.info("[%s] ttyd→client loop ended: %s (after %d msgs)", tag, close_reason, ttyd_msgs)

    c2t = asyncio.create_task(client_to_ttyd(), name=f"{tag}:c2t")
    t2c = asyncio.create_task(ttyd_to_client(), name=f"{tag}:t2c")

    try:
        done, pending = await asyncio.wait(
            [c2t, t2c], return_when=asyncio.FIRST_COMPLETED
        )
        finished = [t.get_name() for t in done]
        waiting = [t.get_name() for t in pending]
        logger.info("[%s] relay: finished=%s pending=%s (client_msgs=%d ttyd_msgs=%d)",
                    tag, finished, waiting, client_msgs, ttyd_msgs)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logger.error("[%s] relay wait error: %s: %s", tag, type(e).__name__, e)
        for task in [c2t, t2c]:
            task.cancel()

    return close_reason


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
