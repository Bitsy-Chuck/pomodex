# KasmVNC Browser Streaming

Stream a full browser (Chromium) running inside the sandbox to the user's browser via KasmVNC.

## Architecture

```
Browser (user)
  |
  +-- Tab 1: Terminal (existing, xterm.js via WebSocket)
  +-- Tab 2: Browser  (new, KasmVNC JS client via WebSocket)
         |
         WebSocket("ws://host:8000/ws/browser/{project_id}?token={jwt}")
         |
         project-service:8000  (routes/browser.py)
           |  accepts browser WS, connects upstream
           |
           ws://terminal-proxy:9000/browser/{project_id}?token={jwt}
           |  (internal, platform-net)
           |
           terminal-proxy  (proxy.py — new /browser/ path handler)
             |  validates JWT (same as terminal)
             |  looks up sandbox IP (same container_lookup)
             |  ensures proxy is on sandbox network (same _ensure_on_network)
             |
             ws://{container_ip}:6901/  (KasmVNC WebSocket)
             |
             KasmVNC server inside sandbox
               |
               Xvfb :1 (virtual display 1280x720)
               |
               Chromium (kiosk or windowed)
```

Same double-proxy pattern as the terminal: browser -> project-service -> terminal-proxy -> sandbox.
Terminal-proxy already handles sandbox network discovery and JWT auth; we reuse all of that.

## Changes by File

### 1. `backend/sandbox/Dockerfile`

Install KasmVNC and Chromium. KasmVNC provides its own VNC server with built-in WebSocket support (port 6901 by default).

```dockerfile
# After the existing apt-get block, add:

# KasmVNC — browser streaming over WebSocket
ARG TARGETARCH
RUN KASMVNC_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "amd64") && \
    wget -qO /tmp/kasmvnc.deb \
    "https://github.com/kasmtech/KasmVNC/releases/download/v1.3.3/kasmvncserver_noble_${KASMVNC_ARCH}.deb" && \
    apt-get update && apt-get install -y /tmp/kasmvnc.deb && \
    rm /tmp/kasmvnc.deb && rm -rf /var/lib/apt/lists/*

# Chromium browser + minimal X dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-browser \
    xfonts-base \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*
```

Add to EXPOSE line:
```dockerfile
EXPOSE 22 7681 6901
```

**Image size impact:** ~200-300MB added (Chromium ~150MB, KasmVNC ~50MB, X libs ~50MB).

### 2. `backend/sandbox/config/supervisord.conf`

Add two new supervised processes: KasmVNC server and Chromium.

```ini
[program:kasmvnc]
command=/usr/bin/kasmvncserver :1
    -geometry 1280x720
    -depth 24
    -websocketPort 6901
    -SecurityTypes None
    -fg
user=agent
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopwaitsecs=5
environment=HOME="/home/agent",DISPLAY=":1"

[program:chromium]
command=chromium-browser
    --no-sandbox
    --disable-gpu
    --display=:1
    --start-maximized
    --no-first-run
    --disable-default-apps
user=agent
autostart=true
autorestart=true
startsecs=3
stopasgroup=true
killasgroup=true
stopwaitsecs=5
environment=HOME="/home/agent",DISPLAY=":1"
```

Notes:
- `-SecurityTypes None` disables VNC password. Auth is handled by terminal-proxy JWT validation, same as the terminal. The VNC port is never exposed outside the sandbox network.
- `-fg` keeps KasmVNC in foreground for supervisor.
- Chromium `--no-sandbox` is required inside Docker (the container itself is the sandbox).
- `startsecs=3` on Chromium gives KasmVNC time to start the display first.

**Alternative:** If `startsecs` is fragile, use a wrapper script that waits for display :1 before launching Chromium:

```bash
#!/bin/bash
# scripts/start-chromium.sh
for i in $(seq 1 30); do
    xdpyinfo -display :1 >/dev/null 2>&1 && break
    sleep 0.5
done
exec chromium-browser --no-sandbox --disable-gpu --display=:1 --start-maximized --no-first-run --disable-default-apps
```

### 3. `backend/terminal_proxy/proxy.py`

Extend `parse_ws_url` and `handle_connection` to support a `/browser/{project_id}` path alongside the existing `/terminal/{project_id}`.

```python
# Updated parse_ws_url
def parse_ws_url(path: str) -> tuple[str | None, str | None, str | None]:
    """Extract service, project_id, and token from WebSocket URL path.

    Supported formats:
      /terminal/{project_id}?token={jwt}
      /browser/{project_id}?token={jwt}

    Returns (service, project_id, token).
    """
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")

    if len(parts) != 2 or parts[0] not in ("terminal", "browser"):
        return None, None, None

    service = parts[0]
    project_id = parts[1]
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]

    return service, project_id, token
```

In `handle_connection`, after looking up the container IP, branch on service type:

```python
# After container_ip lookup succeeds:

if service == "terminal":
    backend_url = f"ws://{container_ip}:{TTYD_PORT}/ws"
    subprotocols = ["tty"]
elif service == "browser":
    backend_url = f"ws://{container_ip}:{KASMVNC_PORT}/"
    subprotocols = []
```

New constant:
```python
KASMVNC_PORT = int(os.environ.get("KASMVNC_PORT", "6901"))
```

The relay logic (`_proxy`) is identical — bidirectional binary WebSocket forwarding. No changes needed to `_proxy()` itself.

Audit logging: KasmVNC traffic is binary pixel data, not terminal keystrokes. Skip audit logging for browser connections (or log connect/disconnect only).

### 4. `backend/project_service/routes/browser.py` (new file)

WebSocket proxy for browser connections. Mirror of `routes/terminal.py` with a different upstream path.

```python
"""WebSocket proxy for browser (KasmVNC) connections.

Routes browser WebSocket through project-service to terminal-proxy
via internal Docker network (platform-net).
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import websockets

logger = logging.getLogger(__name__)

TERMINAL_PROXY_WS = "ws://terminal-proxy:9000"

router = APIRouter()


@router.websocket("/ws/browser/{project_id}")
async def browser_proxy(ws: WebSocket, project_id: str):
    token = ws.query_params.get("token", "")
    await ws.accept()

    upstream_url = f"{TERMINAL_PROXY_WS}/browser/{project_id}?token={token}"
    logger.info("[ws-browser-proxy] connecting upstream for project %s", project_id[:8])

    try:
        async with websockets.connect(upstream_url, open_timeout=10) as upstream:
            logger.info("[ws-browser-proxy] upstream connected for project %s", project_id[:8])

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
        logger.warning("[ws-browser-proxy] upstream rejected project %s: %s", project_id[:8], e)
    except Exception as e:
        logger.error("[ws-browser-proxy] error for project %s: %s: %s", project_id[:8], type(e).__name__, e)

    try:
        await ws.close()
    except Exception:
        pass
```

### 5. `backend/project_service/main.py`

Register the new browser router.

```python
from backend.project_service.routes.browser import router as browser_router
# ...
app.include_router(browser_router)
```

### 6. `backend/project_service/routes/projects.py`

Add `browser_url` to project detail response, same pattern as `terminal_url`.

```python
def _browser_url(project_id: uuid.UUID) -> str:
    return f"ws://{HOST_IP}:{PROJECT_SERVICE_PORT}/ws/browser/{project_id}"


def _project_detail(p: Project) -> dict:
    return {
        # ... existing fields ...
        "terminal_url": _terminal_url(p.id) if p.status == "running" else None,
        "browser_url": _browser_url(p.id) if p.status == "running" else None,
        # ... rest unchanged ...
    }
```

### 7. `backend/project_service/models/schemas.py`

Add `browser_url` to `ProjectDetailResponse`.

```python
class ProjectDetailResponse(ProjectResponse):
    terminal_url: str | None = None
    browser_url: str | None = None       # <-- new
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_user: str = "agent"
    ssh_private_key: str | None = None
    last_backup_at: datetime | None = None
    last_snapshot_at: datetime | None = None
```

### 8. `sandbox-web/src/api/client.ts`

Add `browser_url` to the `ProjectDetail` interface.

```typescript
export interface ProjectDetail extends ProjectSummary {
  terminal_url: string | null
  browser_url: string | null        // <-- new
  ssh_host: string | null
  ssh_port: number | null
  ssh_user: string
  ssh_private_key: string | null
  last_backup_at: string | null
  last_snapshot_at: string | null
}
```

### 9. `sandbox-web/src/components/BrowserView.tsx` (new file)

KasmVNC client component. KasmVNC ships a JavaScript client library (`noVNC` fork). We use it via the npm package `@aspect-build/aspect-vnc` or load KasmVNC's built-in JS client directly.

Simplest approach: use an iframe pointing at KasmVNC's built-in web client, or embed the noVNC JS library.

```tsx
import { useEffect, useRef } from 'react'

interface BrowserViewProps {
  wsUrl: string
  onDisconnect?: () => void
}

export default function BrowserView({ wsUrl, onDisconnect }: BrowserViewProps) {
  const canvasRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!canvasRef.current) return

    // Dynamic import of @novnc/novnc (KasmVNC is noVNC-compatible)
    import('@novnc/novnc/core/rfb').then(({ default: RFB }) => {
      const rfb = new RFB(canvasRef.current!, wsUrl, {
        wsProtocols: [],
      })
      rfb.scaleViewport = true
      rfb.resizeSession = true

      rfb.addEventListener('disconnect', () => {
        onDisconnect?.()
      })

      return () => {
        rfb.disconnect()
      }
    })
  }, [wsUrl, onDisconnect])

  return (
    <div
      ref={canvasRef}
      style={{ width: '100%', height: '100%', minHeight: 500, background: '#1a1a2e' }}
    />
  )
}
```

**npm dependency:** `npm install @novnc/novnc`

Note: If KasmVNC's protocol extensions cause compatibility issues with vanilla noVNC, use KasmVNC's own JS client from their CDN or vendor it. Their client is a fork of noVNC with added features (clipboard sync, WebP encoding, etc.) but the base protocol is compatible.

### 10. `sandbox-web/src/pages/ProjectDetailPage.tsx`

Add a tab/toggle to switch between Terminal and Browser views.

```tsx
import BrowserView from '../components/BrowserView'

// Inside component, add state:
const [activeTab, setActiveTab] = useState<'terminal' | 'browser'>('terminal')

// In the JSX, replace the terminal-only section:
{project.status === 'running' && (() => {
  const token = getAccessToken()
  return (
    <div>
      <div style={{ display: 'flex', gap: 0, marginBottom: 0 }}>
        <button
          onClick={() => setActiveTab('terminal')}
          style={{
            padding: '8px 20px', cursor: 'pointer',
            background: activeTab === 'terminal' ? '#2d3748' : '#e2e8f0',
            color: activeTab === 'terminal' ? '#fff' : '#2d3748',
            border: 'none', borderRadius: '6px 6px 0 0',
          }}
        >
          Terminal
        </button>
        {project.browser_url && (
          <button
            onClick={() => setActiveTab('browser')}
            style={{
              padding: '8px 20px', cursor: 'pointer',
              background: activeTab === 'browser' ? '#2d3748' : '#e2e8f0',
              color: activeTab === 'browser' ? '#fff' : '#2d3748',
              border: 'none', borderRadius: '6px 6px 0 0',
            }}
          >
            Browser
          </button>
        )}
      </div>
      <div style={{
        border: '1px solid #2d3748', borderRadius: '0 6px 6px 6px',
        overflow: 'hidden',
      }}>
        {activeTab === 'terminal' && project.terminal_url && (
          <Terminal wsUrl={`${project.terminal_url}?token=${token}`} />
        )}
        {activeTab === 'browser' && project.browser_url && (
          <BrowserView wsUrl={`${project.browser_url}?token=${token}`} />
        )}
      </div>
    </div>
  )
})()}
```

### 11. `sandbox-web/package.json`

Add noVNC dependency:
```json
"@novnc/novnc": "^1.5.0"
```

## What Does NOT Change

- **docker-compose.yml** — No changes. KasmVNC port 6901 is internal to the sandbox network, never exposed to host. Same as ttyd port 7681.
- **container_lookup.py** — No changes. Same `get_container_ip()` and `_ensure_on_network()` logic.
- **Database model** — No new columns. `browser_url` is computed at response time from project_id, same as `terminal_url`.
- **Auth flow** — Same JWT validation. Browser connections go through the same `validate_token` path.
- **Backup daemon** — No changes. `/home/agent` backup continues as-is. Browser profile lives in `/home/agent/.config/chromium` and gets backed up automatically.

## Resource Considerations (v1, 100 users)

- **Memory:** Chromium adds ~200-500MB per sandbox. Current limit is 1GB. Should increase to **2GB** in `docker_manager.py` (`mem_limit="2g"`).
- **CPU:** VNC encoding is CPU-intensive during screen changes. Current 1 CPU should be fine for casual browsing.
- **Bandwidth:** KasmVNC uses WebP/JPEG encoding. Typical: 0.5-2 Mbps per active session. At 100 concurrent users with ~30% active browsing: ~30-60 Mbps total.
- **Disk:** Chromium profile ~50MB per sandbox. Covered by existing volume.

## Implementation Order

1. Sandbox Dockerfile + supervisor config (add KasmVNC + Chromium)
2. Terminal-proxy changes (add `/browser/` path handling)
3. Project-service browser route + schema changes
4. Frontend BrowserView component + tab UI
5. Test: rebuild sandbox image, create project, verify browser tab works

## Open Questions

- **Default homepage:** Should Chromium open a blank page, or a specific URL?
- **Clipboard sync:** KasmVNC supports bidirectional clipboard. Enable by default or opt-in?
- **Resolution:** Fixed 1280x720, or dynamic based on client viewport size? KasmVNC supports `resizeSession` which adjusts server resolution to match client.
