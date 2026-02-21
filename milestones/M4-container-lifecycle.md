# M4: Container Lifecycle Management

| Field | Value |
|-------|-------|
| **Risk** | HIGH |
| **Dependencies** | M1 (base image) |
| **Blocks** | M5, M6, M7, M8 |
| **Plan sections** | 5.1, 5.5, 7.1 |

---

## Objective

Build the Docker SDK orchestration layer that creates, starts, stops, and
deletes sandbox containers with correct volumes, port mappings, environment
variables, and per-container bridge networks. This is the compute layer that
everything else depends on.

---

## Why This Is High Risk

- Docker SDK edge cases: port conflicts, stale containers, orphaned volumes/networks
- Port allocation: finding free ports, handling race conditions
- Per-container bridge networks: creation, container attachment, cleanup
- Volume lifecycle: create, attach, persist across stop/start, delete
- Container configuration: correct env vars, capabilities, devices, resource limits
- Cleanup: must not leak resources (networks, volumes, containers) on failure paths

---

## Scope

**In scope:**
- Python module for Docker container lifecycle operations
- Volume management (create, delete, attach)
- Port allocation (find free SSH port, handle conflicts)
- Per-container bridge network (create, delete)
- Container creation with all required config (env, volumes, ports, caps, network, limits)
- Container start/stop/delete
- Error handling and resource cleanup on failure

**Out of scope:**
- Snapshot/commit (M5)
- iptables/Squid/bandwidth (M6)
- Full API endpoints (M8)

---

## Deliverables

```
backend/project-service/
  services/docker_manager.py     # Container CRUD, volume, network, port management
  tests/integration/test_docker_manager.py
  tests/unit/test_port_allocation.py
```

---

## Implementation Tasks

1. Implement `find_free_port()` — scan for available host ports in a defined range
2. Implement `create_network(project_id)` — per-container bridge network
3. Implement `create_volume(project_id)` — named volume `vol-{project_id}`
4. Implement `create_container(project_id, config)` — full container creation with all params
5. Implement `start_container(project_id)` — start existing stopped container
6. Implement `stop_container(project_id, timeout=30)` — graceful stop
7. Implement `delete_container(project_id)` — stop + remove container
8. Implement `delete_volume(project_id)` — remove named volume
9. Implement `delete_network(project_id)` — remove bridge network
10. Implement `get_container_ip(project_id)` — get bridge IP for proxy use
11. Implement `cleanup_project_resources(project_id)` — remove container + volume + network
12. Write all tests

---

## Test Cases

### T4.1: Create container with correct configuration
**Type**: Integration (Docker)
**Steps**:
1. Call `create_container("test-proj-1", config)` with full env vars
2. Inspect the running container
**Assert**:
- Container name: `sandbox-test-proj-1`
- Volume `vol-test-proj-1` mounted at `/home/agent`
- SSH port mapped to a host port
- Environment vars set: PROJECT_ID, GCS_BUCKET, GCS_PREFIX, GCS_SA_KEY, SSH_PUBLIC_KEY
- Network: `net-test-proj-1`
- Capabilities: SYS_ADMIN (with all others dropped per deviation fix)
- Devices: `/dev/fuse`
- Memory limit: 1GB
- CPU limit: 1 core

### T4.2: Find free port avoids conflicts
**Type**: Unit test
**Steps**:
1. Bind a socket to a known port
2. Call `find_free_port()` multiple times
**Assert**:
- Never returns the already-bound port
- Returns different ports each time (no duplicates in batch)
- Port is in the expected range (e.g., 30000-60000)

### T4.3: Port allocation handles race condition
**Type**: Integration (Docker)
**Steps**:
1. Call `create_container()` for two projects concurrently
**Assert**:
- Both containers get different SSH ports
- Both containers start successfully
- No "port already in use" errors

### T4.4: Per-container bridge network created
**Type**: Integration (Docker)
**Steps**:
1. Call `create_network("proj-1")`
2. Inspect the network
**Assert**:
- Network `net-proj-1` exists
- Driver is "bridge"
- Network has correct config

### T4.5: Containers on different networks are isolated
**Type**: Integration (Docker)
**Steps**:
1. Create two containers on different bridge networks
2. From container A, try to ping container B's bridge IP
**Assert**:
- Ping fails — containers cannot reach each other
- Each container can only see its own network

### T4.6: Volume persists across container stop/start
**Type**: Integration (Docker)
**Steps**:
1. Create container, write file to `/home/agent/persist-test.txt`
2. Stop container
3. Start container
4. Read the file
**Assert**:
- File content is preserved after stop/start
- Volume remains attached

### T4.7: Stop container is graceful
**Type**: Integration (Docker)
**Steps**:
1. Create and start container
2. Call `stop_container("proj-1", timeout=30)`
3. Check container state
**Assert**:
- Container state is "exited"
- Container received SIGTERM (not SIGKILL) — verify via exit code
- Stop completes within timeout

### T4.8: Delete container removes container only
**Type**: Integration (Docker)
**Steps**:
1. Create container with volume and network
2. Call `delete_container("proj-1")`
**Assert**:
- Container no longer exists
- Volume `vol-proj-1` still exists (intentional — data preserved)
- Network `net-proj-1` still exists (cleaned up separately)

### T4.9: Full cleanup removes all resources
**Type**: Integration (Docker)
**Steps**:
1. Create container + volume + network
2. Call `cleanup_project_resources("proj-1")`
**Assert**:
- Container removed
- Volume removed
- Network removed
- No resources with `proj-1` in name remain

### T4.10: Get container bridge IP
**Type**: Integration (Docker)
**Steps**:
1. Create container on `net-proj-1`
2. Call `get_container_ip("proj-1")`
**Assert**:
- Returns a valid IP address (e.g., `172.x.x.x`)
- IP is on the `net-proj-1` subnet

### T4.11: Create container fails gracefully on duplicate name
**Type**: Integration (Docker)
**Steps**:
1. Create container `sandbox-proj-1`
2. Try to create another container `sandbox-proj-1`
**Assert**:
- Second call raises a clear error (not a Docker API crash)
- First container is unaffected

### T4.12: Resource cleanup on creation failure
**Type**: Integration (Docker)
**Steps**:
1. Attempt to create container with invalid image
**Assert**:
- Container creation fails with clear error
- Network and volume created before the failure are cleaned up
- No orphaned resources

### T4.13: Container resource limits enforced
**Type**: Integration (Docker)
**Steps**:
1. Create container
2. Inspect memory and CPU limits
**Assert**:
- `docker inspect` shows MemoryLimit = 1073741824 (1GB)
- `docker inspect` shows NanoCpus = 1000000000 (1 core)

### T4.14: ttyd port NOT mapped to host
**Type**: Integration (Docker)
**Steps**:
1. Create container
2. Inspect port mappings
**Assert**:
- Port 22 is mapped to a host port (SSH)
- Port 7681 is NOT mapped to any host port (ttyd — proxy-only access)

---

## Acceptance Criteria

- [ ] All 14 test cases pass
- [ ] No resource leaks — containers, volumes, and networks are always cleaned up
- [ ] Port allocation is race-condition-free for up to 10 concurrent creates
- [ ] Container creation takes < 5 seconds (excluding image pull)
- [ ] All Docker operations have proper error handling with descriptive messages
