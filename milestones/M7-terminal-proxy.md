# M7: Terminal Proxy (WebSocket)

| Field | Value |
|-------|-------|
| **Risk** | MEDIUM |
| **Dependencies** | M4 (running containers), M6 (network setup) |
| **Blocks** | M8 |
| **Plan sections** | 7.4, 7.5 |

---

## Objective

Build the Terminal Proxy — an asyncio WebSocket server that authenticates
client connections via JWT, looks up the sandbox container's bridge IP, and
proxies terminal I/O between the client and the sandbox's ttyd instance.
Includes audit logging and connection management.

---

## Why This Is Medium Risk

- WebSocket lifecycle management (connect, disconnect, error, reconnect)
- Bidirectional byte forwarding — must handle binary data, not just text
- JWT validation via HTTP call to Project Service — failure handling
- Docker SDK container IP lookup — container may be stopped or missing
- Concurrent connections to same sandbox
- Connection keepalive and timeout handling

---

## Scope

**In scope:**
- asyncio WebSocket server on port 9000
- JWT validation via `POST /internal/validate` on Project Service
- Container bridge IP lookup via Docker SDK
- WebSocket proxy: client ↔ ttyd bidirectional forwarding
- Audit log (input logging from trusted proxy)
- Connection lifecycle (connect, disconnect, error handling)
- URL parsing: `/terminal/{project_id}?token={jwt}`

**Out of scope:**
- iptables/Squid management (implemented in M6, integrated here)
- Project Service API (M8)
- Rate limiting, command blocking (future features)

---

## Deliverables

```
backend/terminal-proxy/
  proxy.py                          # Main WebSocket server
  services/auth.py                  # JWT validation via Project Service HTTP
  services/container_lookup.py      # Docker SDK container IP lookup
  services/audit.py                 # Audit logging
  tests/integration/test_proxy.py
  tests/unit/test_url_parsing.py
  tests/unit/test_auth.py
  Dockerfile
```

---

## Implementation Tasks

1. Implement URL parser: extract `project_id` and `token` from WebSocket path
2. Implement auth client: `POST /internal/validate` to Project Service
3. Implement container lookup: get bridge IP for `sandbox-{project_id}` on `net-{project_id}`
4. Implement WebSocket proxy: bidirectional forwarding between client and ttyd
5. Implement audit logger: log input messages with project_id, user_id, timestamp
6. Implement connection handler: full lifecycle (auth → lookup → proxy → cleanup)
7. Implement main server: asyncio WebSocket server on port 9000
8. Write Dockerfile for Terminal Proxy
9. Write all tests

---

## Test Cases

### T7.1: URL parsing extracts project_id and token
**Type**: Unit test
**Steps**:
1. Parse `/terminal/abc-123?token=eyJhbG...`
2. Parse `/terminal/abc-123` (no token)
3. Parse `/invalid/path`
**Assert**:
- Case 1: project_id = "abc-123", token = "eyJhbG..."
- Case 2: project_id = "abc-123", token = None
- Case 3: raises error or returns None

### T7.2: Valid JWT — connection accepted
**Type**: Integration (proxy + Project Service mock)
**Setup**: Mock Project Service `/internal/validate` returning 200 + user_id
**Steps**:
1. Connect WebSocket to `ws://localhost:9000/terminal/{project_id}?token={valid_jwt}`
**Assert**:
- WebSocket connection is established (not closed)
- Auth client sent correct request to Project Service

### T7.3: Invalid JWT — connection rejected with 4401
**Type**: Integration (proxy + Project Service mock)
**Setup**: Mock Project Service `/internal/validate` returning 401
**Steps**:
1. Connect WebSocket to `ws://localhost:9000/terminal/{project_id}?token={invalid_jwt}`
**Assert**:
- WebSocket closed with code 4401
- Close reason: "Unauthorized"

### T7.4: Missing token — connection rejected
**Type**: Integration (proxy)
**Steps**:
1. Connect WebSocket to `ws://localhost:9000/terminal/{project_id}` (no token param)
**Assert**:
- WebSocket closed with appropriate error code
- No auth request sent to Project Service

### T7.5: Wrong project ownership — connection rejected
**Type**: Integration (proxy + Project Service mock)
**Setup**: Mock returns 401 (user doesn't own this project)
**Assert**:
- WebSocket closed with 4401
- No connection made to ttyd

### T7.6: Proxy forwards client input to ttyd
**Type**: Integration (proxy + mock ttyd)
**Setup**: Mock ttyd WebSocket server that echoes messages
**Steps**:
1. Establish authenticated connection
2. Send message from client: "ls -la\r"
3. Check what mock ttyd received
**Assert**:
- ttyd received exactly "ls -la\r"
- Byte-level fidelity (no encoding changes)

### T7.7: Proxy forwards ttyd output to client
**Type**: Integration (proxy + mock ttyd)
**Setup**: Mock ttyd sends a predefined message after connection
**Steps**:
1. Establish authenticated connection
2. Read message on client side
**Assert**:
- Client receives the exact bytes sent by mock ttyd
- Binary data is forwarded correctly

### T7.8: Audit log captures input
**Type**: Integration (proxy)
**Steps**:
1. Establish connection
2. Send several input messages
3. Check audit log
**Assert**:
- Each input message logged with: project_id, user_id, timestamp, content
- Output messages are NOT logged (too verbose, also contains ANSI sequences)

### T7.9: Container not running — connection fails gracefully
**Type**: Integration (proxy + Docker)
**Setup**: Project exists but container is stopped
**Steps**:
1. Authenticate successfully
2. Proxy tries to look up container bridge IP
**Assert**:
- WebSocket closed with appropriate error (e.g., 4503 "Container not running")
- No crash or unhandled exception in proxy

### T7.10: Container bridge IP lookup
**Type**: Integration (proxy + Docker)
**Setup**: Running container on `net-{project_id}`
**Assert**:
- Lookup returns correct IP
- IP is on the expected Docker bridge subnet

### T7.11: Connection to ttyd via bridge IP
**Type**: Integration (proxy + Docker + ttyd)
**Setup**: Running sandbox container with ttyd on port 7681
**Steps**:
1. Authenticate
2. Proxy connects to `ws://{container_ip}:7681/ws`
3. Send "echo hello\r" from client
**Assert**:
- Client receives terminal output containing "hello"
- Full round-trip through proxy → ttyd → tmux → bash works

### T7.12: Client disconnect — proxy cleans up
**Type**: Integration (proxy)
**Steps**:
1. Establish connection
2. Client closes WebSocket
**Assert**:
- Proxy closes connection to ttyd
- No orphaned tasks or connections
- No error logs

### T7.13: ttyd disconnect — proxy notifies client
**Type**: Integration (proxy)
**Steps**:
1. Establish connection
2. Stop the sandbox container (kills ttyd)
**Assert**:
- Client WebSocket is closed with appropriate code
- Proxy handles the disconnection cleanly

### T7.14: Concurrent connections to same sandbox
**Type**: Integration (proxy)
**Steps**:
1. Connect client A to the same project
2. Connect client B to the same project
**Assert**:
- Both connections work independently
- Input from client A doesn't leak to client B's audit log
- Both see the same tmux session (ttyd serves the same session)

### T7.15: last_connection_at updated on connect
**Type**: Integration (proxy + Project Service)
**Steps**:
1. Establish connection
2. Check Project Service was called to update `last_connection_at`
**Assert**:
- `/internal/validate` response triggers update
- Timestamp is recent (within 5 seconds)

---

## Acceptance Criteria

- [ ] All 15 test cases pass
- [ ] Proxy handles 10 concurrent connections without errors
- [ ] Connection establishment < 500ms (auth + lookup + ttyd connect)
- [ ] No resource leaks on disconnect (tasks, sockets, file handles)
- [ ] Audit log is tamper-proof (written from proxy, not from sandbox)
- [ ] Binary data (terminal escape sequences, UTF-8) forwarded correctly
