"""WebSocket proxy for terminal connections.

Routes browser WebSocket through project-service to terminal-proxy
via internal Docker network (platform-net). This avoids exposing
terminal-proxy's port to the host, which breaks on Docker Desktop
when the proxy container joins sandbox networks.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import websockets

logger = logging.getLogger(__name__)

TERMINAL_PROXY_WS = "ws://terminal-proxy:9000"

router = APIRouter()


@router.websocket("/ws/terminal/{project_id}")
async def terminal_proxy(ws: WebSocket, project_id: str):
    token = ws.query_params.get("token", "")
    await ws.accept()

    upstream_url = f"{TERMINAL_PROXY_WS}/terminal/{project_id}?token={token}"
    logger.info("[ws-proxy] connecting upstream for project %s", project_id[:8])

    try:
        async with websockets.connect(upstream_url, open_timeout=10) as upstream:
            logger.info("[ws-proxy] upstream connected for project %s", project_id[:8])

            async def browser_to_upstream():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if "bytes" in msg and msg["bytes"]:
                            await upstream.send(msg["bytes"])
                        elif "text" in msg and msg["text"]:
                            await upstream.send(msg["text"])
                except (WebSocketDisconnect, websockets.ConnectionClosed):
                    pass

            async def upstream_to_browser():
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_text(msg)
                except (websockets.ConnectionClosed, WebSocketDisconnect):
                    pass

            tasks = [
                asyncio.create_task(browser_to_upstream()),
                asyncio.create_task(upstream_to_browser()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    except websockets.exceptions.InvalidStatus as e:
        logger.warning("[ws-proxy] upstream rejected project %s: %s", project_id[:8], e)
    except Exception as e:
        logger.error("[ws-proxy] error for project %s: %s: %s", project_id[:8], type(e).__name__, e)

    try:
        await ws.close()
    except Exception:
        pass
