# Pomodex Architecture

## System Overview

Pomodex provides isolated Linux sandbox environments accessible via web browser.
Users create projects, each backed by a Docker container with SSH and web terminal access,
persistent storage via GCS, and snapshot/restore capability.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              BROWSER                                    │
│                                                                         │
│   sandbox-web (React SPA)     xterm.js terminal                        │
│   ─────────────────────       ─────────────────                        │
│   Auth, project CRUD          WebSocket terminal I/O                   │
│                                                                         │
└──────────┬──────────────────────────────┬───────────────────────────────┘
           │ REST (HTTP)                  │ WebSocket
           │ :8000                        │ :9000
           ▼                              ▼
┌─────────────────────┐      ┌──────────────────────────┐
│  project-service    │      │    terminal-proxy        │
│  (FastAPI)          │◄─────┤    (asyncio websockets)  │
│                     │      │                          │
│  • /auth/*          │ POST │  • WS /terminal/{id}     │
│  • /projects/*      │ /internal/validate              │
│  • /internal/*      │      │  • JWT validation        │
│                     │      │  • Container IP lookup   │
└────────┬────────────┘      └──────────┬───────────────┘
         │                              │
         │ SQL                          │ WS :7681
         ▼                              ▼
┌─────────────────┐          ┌─────────────────────────┐
│   PostgreSQL    │          │  sandbox-{project_id}   │
│   :5432         │          │  (Ubuntu 24.04)         │
│                 │          │                         │
│  • users        │          │  • ttyd  (terminal)     │
│  • projects     │          │  • sshd  (SSH)          │
│  • refresh_tkns │          │  • backup_daemon (GCS)  │
└─────────────────┘          └─────────────────────────┘
```

---

## Docker Networks

```
┌─────────────────────────────────────────────────────────────────┐
│                      platform-net (bridge)                       │
│                                                                  │
│   ┌────────────┐   ┌──────────────────┐   ┌──────────────────┐ │
│   │  postgres   │   │ project-service  │   │ terminal-proxy   │ │
│   │  :5432      │   │ :8000            │   │ :9000            │ │
│   └────────────┘   └──────────────────┘   └───────┬──────────┘ │
│                                                    │             │
└────────────────────────────────────────────────────┼─────────────┘
                                                     │
                         ┌───────────────────────────┘
                         │  (dynamically joins per-project networks)
                         │
       ┌─────────────────┼─────────────────────────────────────┐
       │                 │    net-{project_id} (bridge)        │
       │                 ▼                                     │
       │   ┌──────────────────┐   ┌────────────────────────┐  │
       │   │ terminal-proxy   │   │ sandbox-{project_id}   │  │
       │   │ (joined at       │   │                        │  │
       │   │  project start)  │   │  ttyd :7681            │  │
       │   └──────────────────┘   │  sshd :22              │  │
       │                          └────────────────────────┘  │
       └───────────────────────────────────────────────────────┘

  Each running project gets its own isolated Docker network.
  terminal-proxy is the only service that bridges platform-net
  and per-project networks.
```

---

## Port Map

```
  HOST                     CONTAINER             PURPOSE
  ─────────────────────────────────────────────────────────
  :8000  ──────────────►  project-service:8000   REST API
  :9000  ──────────────►  terminal-proxy:9000    WebSocket proxy
  :5432  ──────────────►  postgres:5432          Database (internal)
  :3xxxx ──────────────►  sandbox:22             SSH (per-project, 30000-60000)
                          sandbox:7681           ttyd (internal only, via proxy)
```

---

## Authentication Flow

```
  Browser                    project-service               terminal-proxy
    │                              │                              │
    │  POST /auth/login            │                              │
    │  {email, password}           │                              │
    ├─────────────────────────────►│                              │
    │                              │ verify password (bcrypt)     │
    │                              │ create JWT (HS256, 15min)    │
    │                              │ create refresh token (30d)   │
    │◄─────────────────────────────┤                              │
    │  {access_token,              │                              │
    │   refresh_token}             │                              │
    │                              │                              │
    │  GET /projects/{id}          │                              │
    │  Authorization: Bearer {jwt} │                              │
    ├─────────────────────────────►│                              │
    │                              │ decode JWT                   │
    │                              │ check project ownership      │
    │◄─────────────────────────────┤                              │
    │  {terminal_url, ssh_*}       │                              │
    │                              │                              │
    │  WS /terminal/{id}?token=jwt │                              │
    ├──────────────────────────────┼─────────────────────────────►│
    │                              │                              │ parse URL
    │                              │  POST /internal/validate     │ extract token
    │                              │  X-Internal-Secret: {secret} │
    │                              │  {token, project_id}         │
    │                              │◄─────────────────────────────┤
    │                              │ decode JWT                   │
    │                              │ verify project ownership     │
    │                              │ update last_connection_at    │
    │                              ├─────────────────────────────►│
    │                              │  {user_id}                   │ authenticated
    │                              │                              │
```

**Three auth layers:**
- **User auth**: JWT Bearer tokens on all `/projects/*` routes
- **Service auth**: `X-Internal-Secret` header for terminal-proxy → project-service calls
- **Container auth**: SSH Ed25519 keypair per project (generated at creation)

---

## Terminal WebSocket Data Flow

```
  Browser (xterm.js)         terminal-proxy          sandbox (ttyd)
    │                              │                       │
    │  WS connect                  │                       │
    │  ws://.../terminal/{id}      │                       │
    │  ?token={jwt}                │                       │
    ├─────────────────────────────►│                       │
    │                              │ validate JWT          │
    │                              │ lookup container IP   │
    │                              │ (docker.sock)         │
    │                              │                       │
    │                              │ WS connect            │
    │                              │ ws://{ip}:7681/ws     │
    │                              │ subprotocol: "tty"    │
    │                              ├──────────────────────►│
    │                              │                       │
    │  FIRST MESSAGE (no prefix):  │                       │
    │  {"AuthToken":"",            │   relay               │
    │   "columns":84,"rows":25}   ├──────────────────────►│ start tmux
    │                              │                       │
    │                              │◄──────────────────────┤ type '1': window title
    │◄─────────────────────────────┤                       │
    │                              │◄──────────────────────┤ type '2': preferences
    │◄─────────────────────────────┤                       │
    │                              │◄──────────────────────┤ type '0': shell output
    │◄─────────────────────────────┤                       │  (bash prompt, etc.)
    │                              │                       │
    │  KEYBOARD INPUT:             │                       │
    │  ['0'] + utf8 data           │   relay               │
    ├─────────────────────────────►├──────────────────────►│ → tmux → bash
    │                              │                       │
    │  RESIZE:                     │                       │
    │  ['1'] + {"columns":N,       │   relay               │
    │          "rows":M}           ├──────────────────────►│ resize PTY
    │                              │                       │

  Protocol: ttyd 1.7+ (ASCII type bytes)
  ─────────────────────────────────────
  Server → Client:              Client → Server:
    '0' (0x30) = output           '0' (0x30) = input
    '1' (0x31) = window title     '1' (0x31) = resize
    '2' (0x32) = preferences      First msg  = JSON auth+resize
```

---

## Sandbox Container Internals

```
  ┌─────────────────────────────────────────────────────┐
  │  sandbox-{project_id}   (Ubuntu 24.04)              │
  │                                                      │
  │  ┌────────────────────────────────────────────────┐ │
  │  │  supervisord (PID 1)                           │ │
  │  │                                                │ │
  │  │  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │ │
  │  │  │  sshd    │  │  ttyd    │  │  backup     │ │ │
  │  │  │  :22     │  │  :7681   │  │  daemon.py  │ │ │
  │  │  │          │  │          │  │             │ │ │
  │  │  │  pubkey  │  │  writable│  │  rclone     │ │ │
  │  │  │  auth    │  │  tmux    │  │  sync every │ │ │
  │  │  │          │  │  session │  │  5 minutes  │ │ │
  │  │  └──────────┘  └────┬─────┘  └──────┬──────┘ │ │
  │  │                      │               │        │ │
  │  └──────────────────────┼───────────────┼────────┘ │
  │                         │               │          │
  │  ┌──────────────────────▼──┐   ┌────────▼────────┐ │
  │  │  tmux session "main"   │   │  GCS bucket     │ │
  │  │  └── bash shell        │   │  /{project_id}/ │ │
  │  │      └── user commands │   │  workspace/     │ │
  │  └─────────────────────────┘   └─────────────────┘ │
  │                                                      │
  │  Volume: vol-{project_id} → /home/agent              │
  │  FUSE:   gcsfuse → /mnt/gcs                         │
  │  Limits: 1 GB RAM, 1 CPU                            │
  │  Caps:   SYS_ADMIN (for FUSE)                       │
  └──────────────────────────────────────────────────────┘
```

---

## Project Lifecycle

```
                    ┌──────────┐
           create   │ creating │
          ────────► │          │
                    └────┬─────┘
                         │ container ready
                         ▼
                    ┌──────────┐
                    │ running  │◄──────────────────────┐
                    │          │                        │
                    └────┬─────┘                        │
                         │ stop                         │ start
                         ▼                              │
                    ┌──────────────┐                    │
                    │ snapshotting │                    │
                    │              │                    │
                    │ rclone sync  │                    │
                    │ docker commit│                    │
                    │ push to AR   │                    │
                    └────┬─────────┘                    │
                         │                              │
                         ▼                              │
                    ┌──────────┐      ┌────────────┐   │
                    │ stopped  │─────►│ restoring  │───┘
                    │          │start │            │
                    └────┬─────┘      │ pull image │
                         │            │ new container
                         │ delete     │ same volume│
                         ▼            └────────────┘
                    ┌──────────┐
                    │ deleted  │
                    │          │
                    │ rm container, volume,
                    │ network, GCS data,
                    │ AR images, DB record
                    └──────────┘
```

---

## Storage Architecture

```
  ┌─────────────────────────────────────────────────────────────┐
  │                     PER-USER GCP RESOURCES                   │
  │                                                              │
  │  GCS Bucket: {gcp_project}-u-{sha256(user_id)[:12]}        │
  │  Service Account: sa-{sha256(user_id)[:26]}                 │
  │                                                              │
  │  ┌───────────────────────────────────────────────────────┐  │
  │  │  Bucket Layout                                        │  │
  │  │                                                       │  │
  │  │  /{project_id_1}/workspace/     ← rclone backup      │  │
  │  │  /{project_id_2}/workspace/     ← rclone backup      │  │
  │  │  ...                                                  │  │
  │  └───────────────────────────────────────────────────────┘  │
  │                                                              │
  │  Artifact Registry:                                         │
  │  europe-west1-docker.pkg.dev/{project}/sandboxes/           │
  │    {project_id}:latest      ← docker commit snapshot        │
  │    {project_id}:{timestamp} ← versioned snapshot            │
  │                                                              │
  └─────────────────────────────────────────────────────────────┘

  LOCAL DOCKER STORAGE
  ────────────────────
  Volume: vol-{project_id}  →  /home/agent (survives stop/start)
  Network: net-{project_id} →  per-project isolation
```

---

## Secrets

| Secret | Location | Used By |
|--------|----------|---------|
| JWT signing key | `/secrets/jwt-secret` | project-service |
| Internal service secret | `/secrets/internal-secret` | project-service, terminal-proxy |
| GCP SA key (platform) | `/secrets/project-service-sa.json` | project-service |
| GCP SA key (per-user) | DB `users.gcp_sa_key` | sandbox containers (env) |
| SSH keypair (per-project) | DB `projects.ssh_*` | sandbox containers (env) |
