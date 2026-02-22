"""Integration tests for Docker container lifecycle management (M4).
Tests T4.1, T4.3-T4.14.
"""

import concurrent.futures
import ipaddress
import time
import uuid

import docker
import pytest

from backend.project_service.services.docker_manager import (
    cleanup_project_resources,
    create_container,
    create_network,
    create_volume,
    delete_container,
    delete_network,
    delete_volume,
    get_container_ip,
    start_container,
    stop_container,
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

        volume = docker_client.volumes.get(f"vol-{m4_project_id}")
        assert volume.name == f"vol-{m4_project_id}"

    def test_network_still_exists(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        delete_container(m4_project_id)

        network = docker_client.networks.get(f"net-{m4_project_id}")
        assert network.name == f"net-{m4_project_id}"

    def test_delete_idempotent(self, m4_project_id):
        delete_container(m4_project_id)


class TestGetContainerIP:
    """T4.10: Get container bridge IP."""

    def test_returns_valid_ip(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        ip = get_container_ip(m4_project_id)

        parsed = ipaddress.ip_address(ip)
        assert parsed.version == 4

    def test_ip_on_correct_network(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        ip = get_container_ip(m4_project_id)

        container = docker_client.containers.get(f"sandbox-{m4_project_id}")
        expected = container.attrs["NetworkSettings"]["Networks"][f"net-{m4_project_id}"]["IPAddress"]
        assert ip == expected


class TestCleanupProjectResources:
    """T4.9: Full cleanup removes all resources."""

    def test_removes_all_resources(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        cleanup_project_resources(m4_project_id)

        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(f"sandbox-{m4_project_id}")
        with pytest.raises(docker.errors.NotFound):
            docker_client.volumes.get(f"vol-{m4_project_id}")
        with pytest.raises(docker.errors.NotFound):
            docker_client.networks.get(f"net-{m4_project_id}")

    def test_no_resources_with_project_id_remain(self, docker_client, sandbox_image, m4_project_id, m4_container_config, m4_cleanup):
        m4_cleanup.append(m4_project_id)
        create_container(m4_project_id, m4_container_config)

        cleanup_project_resources(m4_project_id)

        containers = docker_client.containers.list(all=True, filters={"name": f"sandbox-{m4_project_id}"})
        assert len(containers) == 0

    def test_cleanup_idempotent(self, m4_project_id):
        cleanup_project_resources(m4_project_id)


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
        script = (
            "import socket; s = socket.socket(); s.settimeout(3)\n"
            "try:\n"
            f"    s.connect(('{ip_b}', 22)); print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print(f'BLOCKED: {e}')\n"
            "finally:\n"
            "    s.close()"
        )
        exit_code, output = container_a.exec_run(
            ["python3", "-c", script],
            user="agent",
        )
        result = output.decode().strip()
        assert "BLOCKED" in result, f"Expected network isolation, got: {result}"


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
