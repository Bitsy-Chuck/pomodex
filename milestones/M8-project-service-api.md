# M8: Project Service API & Authentication

| Field | Value |
|-------|-------|
| **Risk** | MEDIUM |
| **Dependencies** | M2 (GCS), M3 (IAM), M4 (Docker lifecycle), M5 (snapshot), M6 (networking), M7 (terminal proxy) |
| **Blocks** | M9, M10 |
| **Plan sections** | 2, 3.1, 4, 5.3, 8 |

---

## Objective

Build the FastAPI backend that orchestrates everything: user authentication
(JWT), project CRUD, container lifecycle management, snapshot triggers, and
the inactivity checker background task. This integrates all previous milestones
into a single cohesive API.

---

## Why This Is Medium Risk

The individual components are proven by earlier milestones. Risk here is in
integration — wiring them together correctly, handling error cases across
multiple async operations, and ensuring multi-tenancy isolation. The auth
system uses well-understood patterns (bcrypt + JWT) so risk is lower.

---

## Scope

**In scope:**
- FastAPI application skeleton
- PostgreSQL schema (users, refresh_tokens, projects tables)
- Auth endpoints: register, login, refresh
- JWT middleware (access token validation, user_id extraction)
- Project CRUD endpoints (list, create, get, start, stop, delete, snapshot, restore, backup-status)
- Internal endpoints (`/internal/validate`, `/internal/acl/{project_id}`)
- Localhost-only middleware for `/internal/*` routes
- Background task: inactivity checker (30 min idle → auto snapshot)
- Multi-tenancy: `user_id` filter on every project query
- docker-compose.yml for platform infrastructure

**Out of scope:**
- Web/mobile client implementation (M9, M10)
- Individual component implementations (already done in M1-M7)

---

## Deliverables

```
backend/project-service/
  main.py                           # FastAPI app, middleware, startup
  models/
    database.py                     # SQLAlchemy / asyncpg setup
    schemas.py                      # Pydantic request/response models
  routes/
    auth.py                         # /auth/register, /auth/login, /auth/refresh
    projects.py                     # /projects CRUD
    internal.py                     # /internal/validate, /internal/acl
  middleware/
    auth_middleware.py              # JWT validation, user_id injection
    internal_middleware.py          # Localhost-only for /internal/*
  services/
    auth_service.py                # bcrypt, JWT creation/validation
    project_service.py             # Orchestration: create/stop/start/delete/snapshot
    docker_manager.py              # From M4
    snapshot_manager.py            # From M5
    gcp_iam.py                     # From M3
    network_manager.py             # From M6 (called via terminal proxy)
  tasks/
    inactivity_checker.py          # Background task
  tests/
    unit/test_auth_service.py
    unit/test_project_service.py
    integration/test_auth_endpoints.py
    integration/test_project_endpoints.py
    integration/test_internal_endpoints.py
    integration/test_inactivity_checker.py
  requirements.txt
  Dockerfile

docker-compose.yml                  # Project Service + Postgres + Terminal Proxy
```

---

## Implementation Tasks

1. Set up FastAPI skeleton with CORS, error handling
2. Set up PostgreSQL with asyncpg/SQLAlchemy, create schema (users, refresh_tokens, projects)
3. Implement auth service (bcrypt password hashing, JWT creation/validation)
4. Implement auth routes (register, login, refresh)
5. Implement JWT middleware (extract user_id, inject into request)
6. Implement localhost-only middleware for `/internal/*`
7. Implement project service (orchestrates Docker, IAM, snapshot, network managers)
8. Implement project routes (all CRUD endpoints)
9. Implement internal routes (validate, acl)
10. Implement inactivity checker background task
11. Write docker-compose.yml
12. Write all tests

---

## Test Cases

### Auth Tests

#### T8.1: Register new user
**Type**: Integration (API)
**Request**: `POST /auth/register { "email": "test@example.com", "password": "SecurePass123!" }`
**Assert**:
- Status 201
- Response contains `user_id` (UUID)
- User exists in DB with bcrypt password hash
- Password hash is NOT the raw password

#### T8.2: Register duplicate email
**Type**: Integration (API)
**Request**: Register same email twice
**Assert**:
- Second registration returns 409 Conflict
- Original user is unaffected

#### T8.3: Login with valid credentials
**Type**: Integration (API)
**Request**: `POST /auth/login { "email": "test@example.com", "password": "SecurePass123!" }`
**Assert**:
- Status 200
- Response contains `access_token` (JWT, 15 min expiry)
- Response contains `refresh_token` (opaque, 30 day expiry)
- `refresh_token` hash stored in `refresh_tokens` table

#### T8.4: Login with wrong password
**Type**: Integration (API)
**Assert**:
- Status 401
- No tokens returned

#### T8.5: Login with non-existent email
**Type**: Integration (API)
**Assert**:
- Status 401 (same as wrong password — don't leak user existence)

#### T8.6: Refresh token exchange
**Type**: Integration (API)
**Request**: `POST /auth/refresh { "refresh_token": "..." }`
**Assert**:
- Status 200
- New access_token returned
- New refresh_token returned (rotation)
- Old refresh_token is invalidated

#### T8.7: Expired refresh token rejected
**Type**: Integration (API)
**Setup**: Insert a refresh token with `expires_at` in the past
**Assert**:
- Status 401

#### T8.8: JWT middleware extracts user_id
**Type**: Unit test
**Steps**:
1. Create a valid JWT with user_id claim
2. Pass through middleware
**Assert**:
- `request.state.user_id` is set correctly
- Invalid/expired JWTs return 401

#### T8.9: Access token expiry (15 minutes)
**Type**: Unit test
**Steps**:
1. Create token with 15 min expiry
2. Decode immediately — valid
3. Mock time forward 16 minutes
4. Decode — invalid
**Assert**:
- Token valid within window, rejected after

### Project Endpoints

#### T8.10: List projects — only user's own
**Type**: Integration (API)
**Setup**: Two users with projects each
**Request**: `GET /projects` with user A's token
**Assert**:
- Returns only user A's projects
- User B's projects not included
- Projects have: id, name, status, created_at, last_active_at

#### T8.11: Create project
**Type**: Integration (API + Docker + GCP)
**Request**: `POST /projects { "name": "My Agent" }` with valid token
**Assert**:
- Status 201
- Response contains: project_id, status ("running"), ssh info (host, port, user, private_key), terminal_url
- Container `sandbox-{project_id}` is running
- Volume `vol-{project_id}` exists
- Network `net-{project_id}` exists
- GCP service account created
- DB record has all fields populated

#### T8.12: Get project details
**Type**: Integration (API)
**Request**: `GET /projects/{id}` with owner's token
**Assert**:
- Status 200
- Contains: status, terminal_url, ssh info, last_backup_at, last_active_at

#### T8.13: Get project — wrong user
**Type**: Integration (API)
**Request**: `GET /projects/{id}` with non-owner's token
**Assert**:
- Status 404 (not 403 — don't reveal existence)

#### T8.14: Stop project
**Type**: Integration (API + Docker)
**Request**: `POST /projects/{id}/stop`
**Assert**:
- Status 200
- Container stopped
- Final backup completed (last_backup_at updated)
- Status: "stopped" (or "snapshotting" → "stopped")

#### T8.15: Start stopped project
**Type**: Integration (API + Docker)
**Request**: `POST /projects/{id}/start`
**Assert**:
- Status 200
- Container running
- Status: "running"
- SSH and terminal access work

#### T8.16: Delete project — full teardown
**Type**: Integration (API + Docker + GCP)
**Request**: `DELETE /projects/{id}`
**Assert**:
- Status 200
- Container removed
- Volume removed
- Network removed
- GCP service account deleted
- Artifact Registry images deleted (if any)
- DB record deleted

#### T8.17: Snapshot project
**Type**: Integration (API + Docker + GCP)
**Request**: `POST /projects/{id}/snapshot`
**Assert**:
- Status 200
- Snapshot image pushed to Artifact Registry
- `snapshot_image` field updated in DB
- `last_snapshot_at` updated
- Container stopped after snapshot

#### T8.18: Restore project
**Type**: Integration (API + Docker)
**Request**: `POST /projects/{id}/restore`
**Setup**: Project was stopped with a snapshot
**Assert**:
- Status 200
- Container running from snapshot image
- All system state preserved (installed packages)
- Volume data preserved

#### T8.19: Backup status
**Type**: Integration (API)
**Request**: `GET /projects/{id}/backup-status`
**Assert**:
- Returns: last_backup_at, snapshot info, GCS usage estimate

### Internal Endpoints

#### T8.20: /internal/validate — valid token + ownership
**Type**: Integration (API)
**Request**: `POST /internal/validate { "token": "valid_jwt", "project_id": "owned_project" }`
**Source**: localhost (127.0.0.1)
**Assert**:
- Status 200
- Response: `{ "user_id": "..." }`
- `last_connection_at` updated on the project

#### T8.21: /internal/validate — valid token + wrong project
**Type**: Integration (API)
**Assert**:
- Status 401

#### T8.22: /internal/validate — from external IP
**Type**: Integration (API)
**Source**: Any IP other than 127.0.0.1
**Assert**:
- Status 404 (not 403 — don't reveal route)

#### T8.23: /internal/* middleware blocks external access
**Type**: Integration (API)
**Steps**: Request any /internal/* route from non-localhost
**Assert**:
- All return 404

### Background Tasks

#### T8.24: Inactivity checker identifies idle projects
**Type**: Integration (background task)
**Setup**: Project with `last_connection_at` > 30 minutes ago, status "running"
**Assert**:
- Inactivity checker triggers snapshot for the idle project
- Project status transitions to "stopped" after snapshot

#### T8.25: Inactivity checker skips active projects
**Type**: Integration (background task)
**Setup**: Project with `last_connection_at` < 5 minutes ago
**Assert**:
- Not snapshotted
- Status remains "running"

#### T8.26: Inactivity checker skips non-running projects
**Type**: Integration (background task)
**Setup**: Project with status "stopped" and old `last_connection_at`
**Assert**:
- Not processed (already stopped)

### Error Handling

#### T8.27: Create project — Docker failure
**Type**: Integration (API)
**Setup**: Docker daemon unreachable or image missing
**Assert**:
- Status 500 with descriptive error
- Partial resources cleaned up (no leaked volumes/networks)
- Project status set to "error"

#### T8.28: Create project — GCP IAM failure
**Type**: Integration (API)
**Setup**: GCP API returns error
**Assert**:
- Status 500 with descriptive error
- Any created Docker resources cleaned up
- Project status set to "error"

---

## Acceptance Criteria

- [ ] PostgreSQL schema created and migrations work
- [ ] All 28 test cases pass
- [ ] Auth flow complete: register → login → use token → refresh → continue
- [ ] Multi-tenancy enforced: user can never access another user's project
- [ ] Project lifecycle complete: create → use → stop → restore → delete
- [ ] Inactivity checker runs and auto-snapshots idle projects
- [ ] docker-compose up brings up Project Service + Postgres + Terminal Proxy
- [ ] All error paths clean up resources (no leaked containers/volumes/networks/SAs)
