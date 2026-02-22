# M4: Container Lifecycle Management — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Docker SDK orchestration layer that creates, starts, stops, and deletes sandbox containers with correct volumes, port mappings, environment variables, and per-container bridge networks.

**Architecture:** A single Python module (`docker_manager.py`) exposes low-level building blocks (`create_network`, `create_volume`, etc.) and a high-level `create_container` that orchestrates them with cleanup-on-failure. The module uses the Docker SDK (`docker` pip package) and talks to the local Docker daemon. No manual IPAM — Docker auto-assigns `/24` subnets from a preconfigured address pool.

**Tech Stack:** Python 3.11, docker-py 7.x, pytest

**Decisions made:**
- `cap_add=["SYS_ADMIN"]` only (no `cap_drop=ALL` — deferred to hardening pass)
- Docker daemon configured with `/24` address pools (no manual IPAM in code)
- No proxy env vars (HTTP_PROXY etc.) — that's M6 networking scope
- File paths follow existing convention (`backend/project_service/services/` not `backend/project-service/`)

---

## Prerequisite: Docker daemon address pool config

Before any integration tests, the Docker daemon must be configured to allocate `/24` subnets (not the default `/16`). Without this, Docker exhausts its address space after ~15 bridge networks.

**File:** Docker Desktop > Settings > Docker Engine, or `/etc/docker/daemon.json` on Linux

```json
{
  "default-address-pools": [
    {"base": "172.16.0.0/12", "size": 24}
  ]
}
```

Restart Docker after applying. This gives 4,096 possible `/24` subnets — enough for 500+ concurrent sandboxes.

Also update `GCP_SETUP.md` to document this for production VMs.

---

## Task 1: Scaffolding

**Files:**
- Create: `backend/project_service/services/docker_manager.py`
- Create: `tests/unit/test_port_allocation.py`
- Create: `tests/integration/test_docker_manager.py`
- Modify: `tests/integration/conftest.py` (add M4 fixtures)
- Modify: `GCP_SETUP.md` (add Docker daemon config)

**Step 1: Update GCP_SETUP.md**

Add a "Docker Daemon Configuration" section documenting the address pool setting:

```markdown
## Docker Daemon Configuration

Configure Docker to allocate /24 subnets for bridge networks (default is /16, which exhausts address space after ~15 networks).

**Docker Desktop (dev):** Settings > Docker Engine, add to JSON:

**Linux (production):** Edit `/etc/docker/daemon.json`:

```json
{
  "default-address-pools": [
    {"base": "172.16.0.0/12", "size": 24}
  ]
}
```

Restart Docker after applying. Supports up to 4,096 concurrent bridge networks.
```

**Step 2: Create the docker_manager module skeleton**

```python
# backend/project_service/services/docker_manager.py
"""
Docker container lifecycle management for sandbox containers.

Handles creation, start, stop, deletion of containers with
correct volumes, port mappings, networks, and resource limits.
"""

import logging
import random
import socket

import docker
from docker.errors import APIError, NotFound

logger = logging.getLogger(__name__)

PORT_RANGE_START = 30000
PORT_RANGE_END = 60000
MAX_PORT_RETRIES = 3


def _get_client() -> docker.DockerClient:
    """Get a Docker client from environment."""
    return docker.from_env()
```

**Step 3: Create empty test files**

```python
# tests/unit/test_port_allocation.py
"""Unit tests for port allocation (T4.2)."""

# tests/integration/test_docker_manager.py
"""Integration tests for Docker container lifecycle management (M4).
Tests T4.1, T4.3–T4.14.
"""
```

**Step 4: Add M4 fixtures to conftest.py**

Append to `tests/integration/conftest.py`:

```python
# ---------------------------------------------------------------------------
# M4: Docker lifecycle fixtures
# ---------------------------------------------------------------------------

M4_CONTAINER_PREFIX = "m4-test"


@pytest.fixture()
def m4_project_id():
    """Unique project ID for each M4 test."""
    return f"m4-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def m4_container_config(gcs_sa_key):
    """Standard container config for M4 tests."""
    return {
        "image": IMAGE_NAME,
        "gcs_bucket": GCS_BUCKET,
        "gcs_sa_key": gcs_sa_key,
        "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeTestKey test@test",
    }


@pytest.fixture(autouse=False)
def m4_cleanup(docker_client):
    """Track and clean up all Docker resources created during M4 tests.

    Usage: append project IDs to the returned list. Cleanup runs after test.
    """
    project_ids = []
    yield project_ids
    for pid in project_ids:
        for name, getter, force in [
            (f"sandbox-{pid}", docker_client.containers, True),
            (f"vol-{pid}", docker_client.volumes, True),
            (f"net-{pid}", docker_client.networks, False),
        ]:
            try:
                obj = getter.get(name)
                if force:
                    obj.remove(force=True)
                else:
                    obj.remove()
            except Exception:
                pass
```

**Step 5: Verify scaffolding**

Run: `cd /Users/air/Dropbox/air/projects/pomodex/.worktrees/m4-container-lifecycle && python3 -c "from backend.project_service.services.docker_manager import _get_client; print('import OK')"`

Expected: `import OK`

**Step 6: Commit**

```
feat(m4): scaffolding for container lifecycle module

Add docker_manager.py skeleton, empty test files, M4 fixtures
in conftest, and Docker daemon address pool config in GCP_SETUP.md.
```

---

## Task 2: find_free_port — Unit Tests + Implementation (T4.2)

**Files:**
- Modify: `tests/unit/test_port_allocation.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_port_allocation.py
"""Unit tests for port allocation (T4.2)."""

import socket

import pytest

from backend.project_service.services.docker_manager import (
    find_free_port,
    PORT_RANGE_START,
    PORT_RANGE_END,
)


class TestFindFreePort:
    """T4.2: Find free port avoids conflicts."""

    def test_returns_port_in_expected_range(self):
        port = find_free_port()
        assert PORT_RANGE_START <= port <= PORT_RANGE_END

    def test_never_returns_already_bound_port(self):
        """Bind a socket to a known port, verify find_free_port skips it."""
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound.bind(("0.0.0.0", 30000))
        bound.listen(1)
        try:
            for _ in range(20):
                port = find_free_port(start=30000, end=30020)
                assert port != 30000
        finally:
            bound.close()

    def test_no_duplicates_when_ports_held(self):
        """Bind each returned port before calling again — all must differ."""
        held_sockets = []
        ports = []
        try:
            for _ in range(5):
                port = find_free_port(start=40000, end=40100)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.listen(1)
                held_sockets.append(s)
                ports.append(port)
            assert len(set(ports)) == 5
        finally:
            for s in held_sockets:
                s.close()

    def test_raises_when_range_exhausted(self):
        """All ports in a tiny range bound — should raise RuntimeError."""
        held = []
        try:
            for p in range(50000, 50003):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", p))
                s.listen(1)
                held.append(s)
            with pytest.raises(RuntimeError, match="No free port"):
                find_free_port(start=50000, end=50002)
        finally:
            for s in held:
                s.close()
```

**Step 2: Run tests to verify they fail**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/unit/test_port_allocation.py -v`

Expected: All 4 tests FAIL with `ImportError` or `AttributeError` (function doesn't exist yet)

**Step 3: Write minimal implementation**

Add to `docker_manager.py`:

```python
def find_free_port(start: int = PORT_RANGE_START, end: int = PORT_RANGE_END) -> int:
    """Find a free TCP port in the given range.

    Binds a socket to verify availability. Ports are tried in random
    order to reduce contention under concurrent calls.

    Raises RuntimeError if no port is free in the range.
    """
    ports = list(range(start, end + 1))
    random.shuffle(ports)
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found in range {start}-{end}")
```

**Step 4: Run tests to verify they pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/unit/test_port_allocation.py -v`

Expected: All 4 PASS

**Step 5: Commit**

```
feat(m4): implement find_free_port with unit tests (T4.2)
```

---

## Task 3: create_network / delete_network (T4.4)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing test**

```python
# tests/integration/test_docker_manager.py
"""Integration tests for Docker container lifecycle management (M4)."""

import uuid

import docker
import pytest

from backend.project_service.services.docker_manager import (
    create_network,
    delete_network,
)


class TestCreateNetwork:
    """T4.4: Per-container bridge network created."""

    def test_creates_bridge_network_with_correct_name(self, docker_client, m4_project_id, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        name = create_network(m4_project_id)

        assert name == f"net-{m4_project_id}"
        network = docker_client.networks.get(name)
        assert network.attrs["Driver"] == "bridge"

    def test_ipv6_disabled(self, docker_client, m4_project_id, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_network(m4_project_id)

        network = docker_client.networks.get(f"net-{m4_project_id}")
        assert network.attrs["EnableIPv6"] is False

    def test_delete_network_removes_it(self, docker_client, m4_project_id, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_network(m4_project_id)
        delete_network(m4_project_id)

        with pytest.raises(docker.errors.NotFound):
            docker_client.networks.get(f"net-{m4_project_id}")

    def test_delete_network_idempotent(self, m4_project_id):
        """Deleting a non-existent network should not raise."""
        delete_network(m4_project_id)  # no error
```

**Step 2: Run tests to verify they fail**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateNetwork -v`

Expected: FAIL — `create_network` not importable

**Step 3: Write minimal implementation**

Add to `docker_manager.py`:

```python
def create_network(project_id: str) -> str:
    """Create a per-container bridge network. Returns the network name."""
    client = _get_client()
    name = f"net-{project_id}"
    client.networks.create(name, driver="bridge", enable_ipv6=False)
    return name


def delete_network(project_id: str) -> None:
    """Remove the bridge network for a project. Idempotent."""
    client = _get_client()
    try:
        network = client.networks.get(f"net-{project_id}")
        network.remove()
    except NotFound:
        pass
```

**Step 4: Run tests to verify they pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateNetwork -v`

Expected: All 4 PASS

**Step 5: Commit**

```
feat(m4): implement create/delete network with tests (T4.4)
```

---

## Task 4: create_volume / delete_volume

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing test**

```python
from backend.project_service.services.docker_manager import (
    create_network,
    create_volume,
    delete_network,
    delete_volume,
)


class TestCreateVolume:

    def test_creates_named_volume(self, docker_client, m4_project_id, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        name = create_volume(m4_project_id)

        assert name == f"vol-{m4_project_id}"
        volume = docker_client.volumes.get(name)
        assert volume.name == name

    def test_delete_volume_removes_it(self, docker_client, m4_project_id, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_volume(m4_project_id)
        delete_volume(m4_project_id)

        with pytest.raises(docker.errors.NotFound):
            docker_client.volumes.get(f"vol-{m4_project_id}")

    def test_delete_volume_idempotent(self, m4_project_id):
        delete_volume(m4_project_id)  # no error
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateVolume -v`

Expected: FAIL — `create_volume` not importable

**Step 3: Write minimal implementation**

```python
def create_volume(project_id: str) -> str:
    """Create a named volume for a project. Returns the volume name."""
    client = _get_client()
    name = f"vol-{project_id}"
    client.volumes.create(name=name)
    return name


def delete_volume(project_id: str) -> None:
    """Remove the named volume for a project. Idempotent."""
    client = _get_client()
    try:
        volume = client.volumes.get(f"vol-{project_id}")
        volume.remove()
    except NotFound:
        pass
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateVolume -v`

Expected: All 3 PASS

**Step 5: Commit**

```
feat(m4): implement create/delete volume with tests
```

---

## Task 5: create_container — Happy Path (T4.1, T4.13, T4.14)

This is the big one. `create_container` orchestrates network + volume + container creation.

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing tests**

```python
import time

from backend.project_service.services.docker_manager import (
    create_container,
    create_network,
    create_volume,
    delete_network,
    delete_volume,
)


class TestCreateContainer:
    """T4.1: Create container with correct configuration.
    T4.13: Container resource limits enforced.
    T4.14: ttyd port NOT mapped to host.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        """Store common fixtures and register cleanup."""
        self.client = docker_client
        self.project_id = m4_project_id
        self.config = m4_container_config
        m4_cleanup.append(m4_project_id)

    def _create_and_get(self):
        container_id, ssh_port = create_container(self.project_id, self.config)
        container = self.client.containers.get(container_id)
        return container, ssh_port

    def test_container_name(self):
        container, _ = self._create_and_get()
        assert container.name == f"sandbox-{self.project_id}"

    def test_volume_mounted_at_home_agent(self):
        container, _ = self._create_and_get()
        mounts = container.attrs["Mounts"]
        vol_mount = [m for m in mounts if m["Destination"] == "/home/agent"]
        assert len(vol_mount) == 1
        assert vol_mount[0]["Name"] == f"vol-{self.project_id}"

    def test_ssh_port_mapped(self):
        container, ssh_port = self._create_and_get()
        ports = container.attrs["NetworkSettings"]["Ports"]
        assert "22/tcp" in ports
        host_port = int(ports["22/tcp"][0]["HostPort"])
        assert host_port == ssh_port
        assert 30000 <= host_port <= 60000

    def test_environment_vars_set(self):
        container, _ = self._create_and_get()
        env = dict(e.split("=", 1) for e in container.attrs["Config"]["Env"])
        assert env["PROJECT_ID"] == self.project_id
        assert env["GCS_BUCKET"] == self.config["gcs_bucket"]
        assert env["GCS_PREFIX"] == f"projects/{self.project_id}"
        assert "GCS_SA_KEY" in env
        assert "SSH_PUBLIC_KEY" in env

    def test_network_attached(self):
        container, _ = self._create_and_get()
        networks = container.attrs["NetworkSettings"]["Networks"]
        assert f"net-{self.project_id}" in networks

    def test_cap_add_sys_admin(self):
        container, _ = self._create_and_get()
        host_config = container.attrs["HostConfig"]
        assert "SYS_ADMIN" in (host_config.get("CapAdd") or [])

    def test_fuse_device(self):
        container, _ = self._create_and_get()
        devices = container.attrs["HostConfig"].get("Devices") or []
        fuse_devs = [d for d in devices if "/dev/fuse" in d.get("PathOnHost", "")]
        assert len(fuse_devs) == 1

    def test_memory_limit_1gb(self):
        """T4.13: MemoryLimit = 1073741824 (1GB)."""
        container, _ = self._create_and_get()
        mem = container.attrs["HostConfig"]["Memory"]
        assert mem == 1073741824

    def test_cpu_limit_1_core(self):
        """T4.13: NanoCpus = 1000000000 (1 core)."""
        container, _ = self._create_and_get()
        nano = container.attrs["HostConfig"]["NanoCpus"]
        assert nano == 1_000_000_000

    def test_ttyd_port_not_mapped(self):
        """T4.14: Port 7681 (ttyd) is NOT mapped to any host port."""
        container, _ = self._create_and_get()
        ports = container.attrs["NetworkSettings"]["Ports"]
        ttyd_mapping = ports.get("7681/tcp")
        assert ttyd_mapping is None
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateContainer -v`

Expected: FAIL — `create_container` not importable

**Step 3: Write minimal implementation**

```python
def create_container(project_id: str, config: dict) -> tuple:
    """Create a sandbox container with full configuration.

    Orchestrates: create network -> create volume -> find port -> run container.
    On failure, cleans up any resources created before the failure point.

    config keys:
        image: str          - Docker image name
        gcs_bucket: str     - GCS bucket name
        gcs_sa_key: str     - SA key JSON string
        ssh_public_key: str - SSH public key

    Returns (container_id: str, ssh_port: int).
    """
    client = _get_client()
    network_created = False
    volume_created = False

    try:
        create_network(project_id)
        network_created = True

        create_volume(project_id)
        volume_created = True

        last_error = None
        for attempt in range(MAX_PORT_RETRIES):
            ssh_port = find_free_port()
            try:
                container = client.containers.run(
                    image=config["image"],
                    name=f"sandbox-{project_id}",
                    detach=True,
                    volumes={
                        f"vol-{project_id}": {"bind": "/home/agent", "mode": "rw"},
                    },
                    ports={"22/tcp": ssh_port},
                    environment={
                        "PROJECT_ID": str(project_id),
                        "GCS_BUCKET": config["gcs_bucket"],
                        "GCS_PREFIX": f"projects/{project_id}",
                        "GCS_SA_KEY": config["gcs_sa_key"],
                        "SSH_PUBLIC_KEY": config["ssh_public_key"],
                    },
                    network=f"net-{project_id}",
                    cap_add=["SYS_ADMIN"],
                    devices=["/dev/fuse"],
                    security_opt=["apparmor:unconfined"],
                    mem_limit="1g",
                    nano_cpus=1_000_000_000,
                )
                return container.id, ssh_port
            except APIError as e:
                if "port is already allocated" in str(e).lower() and attempt < MAX_PORT_RETRIES - 1:
                    last_error = e
                    continue
                raise
        raise last_error

    except Exception:
        if volume_created:
            try:
                delete_volume(project_id)
            except Exception:
                pass
        if network_created:
            try:
                delete_network(project_id)
            except Exception:
                pass
        raise
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCreateContainer -v`

Expected: All 11 tests PASS (container creation takes ~2-5s)

**Step 5: Commit**

```
feat(m4): implement create_container with tests (T4.1, T4.13, T4.14)
```

---

## Task 6: stop_container + start_container (T4.7, T4.6)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing tests**

```python
from backend.project_service.services.docker_manager import (
    create_container,
    start_container,
    stop_container,
)


class TestStopContainer:
    """T4.7: Stop container is graceful."""

    def test_stop_sets_exited_state(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        container_id, _ = create_container(m4_project_id, m4_container_config)

        stop_container(m4_project_id, timeout=30)

        container = docker_client.containers.get(container_id)
        container.reload()
        assert container.status == "exited"

    def test_stop_uses_sigterm(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        """Container should receive SIGTERM (exit code 0 or 143), not SIGKILL (137)."""
        m4_cleanup.append(m4_project_id)
        container_id, _ = create_container(m4_project_id, m4_container_config)
        # Give container a moment to start supervisord
        time.sleep(2)

        stop_container(m4_project_id, timeout=30)

        container = docker_client.containers.get(container_id)
        container.reload()
        exit_code = container.attrs["State"]["ExitCode"]
        # SIGTERM = 143 (128+15) or 0 (clean shutdown). SIGKILL = 137.
        assert exit_code != 137, "Container was SIGKILLed, not SIGTERMed"


class TestStartContainer:
    """T4.6: Volume persists across container stop/start."""

    def test_volume_persists_across_stop_start(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        container_id, _ = create_container(m4_project_id, m4_container_config)
        container = docker_client.containers.get(container_id)
        # Wait for container to be ready
        time.sleep(3)

        # Write a file to /home/agent
        container.exec_run("bash -c 'echo persist-test-data > /home/agent/persist-test.txt'", user="agent")

        # Stop
        stop_container(m4_project_id)

        # Start
        start_container(m4_project_id)
        container.reload()
        assert container.status == "running"

        # Wait for container to restart
        time.sleep(3)

        # Read the file back
        exit_code, output = container.exec_run("cat /home/agent/persist-test.txt", user="agent")
        assert exit_code == 0
        assert b"persist-test-data" in output
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestStopContainer tests/integration/test_docker_manager.py::TestStartContainer -v`

Expected: FAIL — functions not importable

**Step 3: Write minimal implementation**

```python
def start_container(project_id: str) -> None:
    """Start a stopped container."""
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    container.start()


def stop_container(project_id: str, timeout: int = 30) -> None:
    """Gracefully stop a running container.

    Sends SIGTERM and waits up to timeout seconds before SIGKILL.
    """
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    container.stop(timeout=timeout)
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestStopContainer tests/integration/test_docker_manager.py::TestStartContainer -v`

Expected: All 3 PASS

**Step 5: Commit**

```
feat(m4): implement start/stop container with tests (T4.6, T4.7)
```

---

## Task 7: delete_container (T4.8)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing test**

```python
from backend.project_service.services.docker_manager import (
    create_container,
    delete_container,
)


class TestDeleteContainer:
    """T4.8: Delete container removes container only."""

    def test_removes_container(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        container_id, _ = create_container(m4_project_id, m4_container_config)

        delete_container(m4_project_id)

        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(f"sandbox-{m4_project_id}")

    def test_volume_still_exists(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        delete_container(m4_project_id)

        # Volume should still exist
        volume = docker_client.volumes.get(f"vol-{m4_project_id}")
        assert volume.name == f"vol-{m4_project_id}"

    def test_network_still_exists(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        delete_container(m4_project_id)

        # Network should still exist
        network = docker_client.networks.get(f"net-{m4_project_id}")
        assert network.name == f"net-{m4_project_id}"

    def test_delete_idempotent(self, m4_project_id):
        """Deleting a non-existent container should not raise."""
        delete_container(m4_project_id)
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestDeleteContainer -v`

Expected: FAIL

**Step 3: Write minimal implementation**

```python
def delete_container(project_id: str) -> None:
    """Stop (if running) and remove a container.

    Does NOT remove volume or network — use cleanup_project_resources for that.
    Idempotent.
    """
    client = _get_client()
    try:
        container = client.containers.get(f"sandbox-{project_id}")
        container.remove(force=True)
    except NotFound:
        pass
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestDeleteContainer -v`

Expected: All 4 PASS

**Step 5: Commit**

```
feat(m4): implement delete_container with tests (T4.8)
```

---

## Task 8: get_container_ip (T4.10)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing test**

```python
import ipaddress

from backend.project_service.services.docker_manager import (
    create_container,
    get_container_ip,
)


class TestGetContainerIP:
    """T4.10: Get container bridge IP."""

    def test_returns_valid_ip(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        ip = get_container_ip(m4_project_id)

        # Should be a valid IPv4 address
        parsed = ipaddress.ip_address(ip)
        assert parsed.version == 4

    def test_ip_on_correct_network(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        ip = get_container_ip(m4_project_id)

        # Verify the IP matches what Docker reports for the container's network
        container = docker_client.containers.get(f"sandbox-{m4_project_id}")
        expected = container.attrs["NetworkSettings"]["Networks"][f"net-{m4_project_id}"]["IPAddress"]
        assert ip == expected
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestGetContainerIP -v`

**Step 3: Write minimal implementation**

```python
def get_container_ip(project_id: str) -> str:
    """Get the bridge network IP address of a container."""
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    network_name = f"net-{project_id}"
    networks = container.attrs["NetworkSettings"]["Networks"]
    if network_name not in networks:
        raise ValueError(f"Container not connected to network {network_name}")
    return networks[network_name]["IPAddress"]
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestGetContainerIP -v`

Expected: All 2 PASS

**Step 5: Commit**

```
feat(m4): implement get_container_ip with tests (T4.10)
```

---

## Task 9: cleanup_project_resources (T4.9)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`
- Modify: `backend/project_service/services/docker_manager.py`

**Step 1: Write the failing test**

```python
from backend.project_service.services.docker_manager import (
    cleanup_project_resources,
    create_container,
)


class TestCleanupProjectResources:
    """T4.9: Full cleanup removes all resources."""

    def test_removes_all_resources(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        # m4_cleanup as safety net — but cleanup_project_resources should handle it
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        cleanup_project_resources(m4_project_id)

        # Container gone
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(f"sandbox-{m4_project_id}")
        # Volume gone
        with pytest.raises(docker.errors.NotFound):
            docker_client.volumes.get(f"vol-{m4_project_id}")
        # Network gone
        with pytest.raises(docker.errors.NotFound):
            docker_client.networks.get(f"net-{m4_project_id}")

    def test_no_resources_with_project_id_remain(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        cleanup_project_resources(m4_project_id)

        # Scan all Docker objects for anything with our project_id
        containers = docker_client.containers.list(all=True, filters={"name": f"sandbox-{m4_project_id}"})
        assert len(containers) == 0
        volumes = docker_client.volumes.list(filters={"name": f"vol-{m4_project_id}"})
        assert len(volumes.get("Volumes") or volumes) == 0

    def test_cleanup_idempotent(self, m4_project_id):
        """Cleaning up non-existent resources should not raise."""
        cleanup_project_resources(m4_project_id)
```

**Step 2: Verify failure**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCleanupProjectResources -v`

**Step 3: Write minimal implementation**

```python
def cleanup_project_resources(project_id: str) -> None:
    """Remove all Docker resources for a project: container, volume, network.

    Idempotent — safe to call even if some or all resources are already gone.
    """
    delete_container(project_id)
    delete_volume(project_id)
    delete_network(project_id)
```

**Step 4: Verify pass**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestCleanupProjectResources -v`

Expected: All 3 PASS

**Step 5: Commit**

```
feat(m4): implement cleanup_project_resources with tests (T4.9)
```

---

## Task 10: Concurrent Port Allocation (T4.3)

No new production code needed — this tests existing create_container with concurrency.

**Files:**
- Modify: `tests/integration/test_docker_manager.py`

**Step 1: Write the test**

```python
import concurrent.futures

from backend.project_service.services.docker_manager import (
    create_container,
    cleanup_project_resources,
)


class TestPortRaceCondition:
    """T4.3: Port allocation handles race condition."""

    def test_concurrent_creates_get_different_ports(self, docker_client, sandbox_image, m4_container_config, m4_cleanup):
        ids = [f"m4-race-{uuid.uuid4().hex[:6]}" for _ in range(2)]
        for pid in ids:
            m4_cleanup.append(pid)

        results = {}
        errors = []

        def _create(pid):
            try:
                _, port = create_container(pid, m4_container_config)
                return pid, port
            except Exception as e:
                return pid, e

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_create, pid): pid for pid in ids}
            for f in concurrent.futures.as_completed(futures):
                pid, result = f.result()
                if isinstance(result, Exception):
                    errors.append((pid, result))
                else:
                    results[pid] = result

        assert len(errors) == 0, f"Container creation failed: {errors}"
        ports = list(results.values())
        assert len(set(ports)) == 2, f"Ports not unique: {ports}"
```

**Step 2: Run the test**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestPortRaceCondition -v`

Expected: PASS (retry logic in create_container handles any port collisions)

**Step 3: Commit**

```
test(m4): add concurrent port allocation test (T4.3)
```

---

## Task 11: Network Isolation (T4.5)

No new production code — tests that containers on different bridge networks are isolated.

**Files:**
- Modify: `tests/integration/test_docker_manager.py`

**Step 1: Write the test**

```python
from backend.project_service.services.docker_manager import (
    create_container,
    get_container_ip,
)


class TestNetworkIsolation:
    """T4.5: Containers on different networks are isolated."""

    def test_containers_cannot_reach_each_other(self, docker_client, sandbox_image, m4_container_config, m4_cleanup):
        id_a = f"m4-iso-a-{uuid.uuid4().hex[:6]}"
        id_b = f"m4-iso-b-{uuid.uuid4().hex[:6]}"
        m4_cleanup.append(id_a)
        m4_cleanup.append(id_b)

        create_container(id_a, m4_container_config)
        create_container(id_b, m4_container_config)

        ip_b = get_container_ip(id_b)
        container_a = docker_client.containers.get(f"sandbox-{id_a}")

        # Wait for containers to be ready
        time.sleep(3)

        # Try to connect from A to B's SSH port — should fail (timeout)
        exit_code, output = container_a.exec_run(
            f"python3 -c \""
            f"import socket; s = socket.socket(); s.settimeout(3); "
            f"try:\n"
            f"    s.connect(('{ip_b}', 22)); print('CONNECTED')\n"
            f"except Exception as e:\n"
            f"    print(f'BLOCKED: {{e}}')\n"
            f"finally:\n"
            f"    s.close()\n"
            f"\"",
            user="agent",
        )
        result = output.decode().strip()
        assert "BLOCKED" in result, f"Expected network isolation, got: {result}"
```

**Step 2: Run the test**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestNetworkIsolation -v`

Expected: PASS (Docker bridge isolation is built-in)

**Step 3: Commit**

```
test(m4): add network isolation test (T4.5)
```

---

## Task 12: Error Handling (T4.11, T4.12)

**Files:**
- Modify: `tests/integration/test_docker_manager.py`

**Step 1: Write the failing tests**

```python
class TestErrorHandling:
    """T4.11: Duplicate name error.
    T4.12: Resource cleanup on creation failure.
    """

    def test_duplicate_name_raises_clear_error(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        """T4.11: Second create with same name raises, first container unaffected."""
        m4_cleanup.append(m4_project_id)
        container_id_1, _ = create_container(m4_project_id, m4_container_config)

        with pytest.raises(Exception) as exc_info:
            create_container(m4_project_id, m4_container_config)

        # First container should still exist and be running
        container_1 = docker_client.containers.get(container_id_1)
        container_1.reload()
        assert container_1.status == "running"

    def test_invalid_image_cleans_up_resources(self, docker_client, m4_project_id, m4_cleanup):
        """T4.12: Creation with invalid image fails, network+volume cleaned up."""
        m4_cleanup.append(m4_project_id)
        bad_config = {
            "image": "nonexistent-image-that-does-not-exist:latest",
            "gcs_bucket": "test",
            "gcs_sa_key": "{}",
            "ssh_public_key": "ssh-ed25519 AAAAC3 test",
        }

        with pytest.raises(Exception):
            create_container(m4_project_id, bad_config)

        # No orphaned network
        with pytest.raises(docker.errors.NotFound):
            docker_client.networks.get(f"net-{m4_project_id}")

        # No orphaned volume
        with pytest.raises(docker.errors.NotFound):
            docker_client.volumes.get(f"vol-{m4_project_id}")
```

**Step 2: Run the test**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/integration/test_docker_manager.py::TestErrorHandling -v`

Expected: PASS — error handling is already implemented in Task 5's `create_container`.

If T4.11 fails (the duplicate name case), the issue is that `create_network` or `create_volume` fails on the second call because the resources already exist. In that case, add duplicate-name detection at the top of `create_container`:

```python
# At the start of create_container, before creating resources:
try:
    client.containers.get(f"sandbox-{project_id}")
    raise ValueError(f"Container sandbox-{project_id} already exists")
except NotFound:
    pass
```

**Step 3: Commit**

```
test(m4): add error handling tests (T4.11, T4.12)
```

---

## Task 13: Final Verification

**Step 1: Run ALL tests**

Run: `cd .worktrees/m4-container-lifecycle && python3 -m pytest tests/unit/test_port_allocation.py tests/integration/test_docker_manager.py -v`

Expected: All 14+ tests PASS across both files

**Step 2: Verify no resource leaks**

Run: `docker ps -a --filter "name=m4-" --filter "name=sandbox-m4" && docker volume ls --filter "name=vol-m4" && docker network ls --filter "name=net-m4"`

Expected: No leftover resources with `m4-` prefix

**Step 3: Final commit (if any refactoring done)**

```
refactor(m4): final cleanup for container lifecycle module
```

---

## Summary: Test Case → Task Mapping

| Test | Description | Task |
|------|-------------|------|
| T4.1 | Create container config | Task 5 |
| T4.2 | find_free_port | Task 2 |
| T4.3 | Port race condition | Task 10 |
| T4.4 | Bridge network | Task 3 |
| T4.5 | Network isolation | Task 11 |
| T4.6 | Volume persists stop/start | Task 6 |
| T4.7 | Graceful stop | Task 6 |
| T4.8 | Delete container only | Task 7 |
| T4.9 | Full cleanup | Task 9 |
| T4.10 | Container IP | Task 8 |
| T4.11 | Duplicate name error | Task 12 |
| T4.12 | Cleanup on failure | Task 12 |
| T4.13 | Resource limits | Task 5 |
| T4.14 | ttyd not mapped | Task 5 |

## Files Created/Modified

| File | Action |
|------|--------|
| `backend/project_service/services/docker_manager.py` | Create |
| `tests/unit/test_port_allocation.py` | Rewrite |
| `tests/integration/test_docker_manager.py` | Create |
| `tests/integration/conftest.py` | Modify (add M4 fixtures) |
| `GCP_SETUP.md` | Modify (add daemon config) |
