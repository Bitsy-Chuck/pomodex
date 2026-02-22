"""Verification tests for docker-compose.yml."""

import subprocess
import os

import pytest
import yaml


# Navigate from this test file up to project root
COMPOSE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "docker-compose.yml"
)


class TestDockerCompose:

    def test_compose_file_exists(self):
        assert os.path.isfile(COMPOSE_FILE), f"docker-compose.yml not found at {COMPOSE_FILE}"

    def test_compose_valid_yaml(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_compose_has_required_services(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        services = data.get("services", {})
        assert "project-service" in services, "Missing project-service"
        assert "postgres" in services, "Missing postgres"
        assert "terminal-proxy" in services, "Missing terminal-proxy"

    def test_project_service_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        ps = data["services"]["project-service"]
        # Must mount docker socket for container management
        volumes = ps.get("volumes", [])
        assert any("/var/run/docker.sock" in str(v) for v in volumes), \
            "project-service must mount Docker socket"
        # Must expose port 8000
        ports = ps.get("ports", [])
        assert any("8000" in str(p) for p in ports), "project-service must expose port 8000"
        # Must depend on postgres
        depends = ps.get("depends_on", {})
        if isinstance(depends, list):
            assert "postgres" in depends
        else:
            assert "postgres" in depends

    def test_postgres_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        pg = data["services"]["postgres"]
        assert pg.get("image", "").startswith("postgres:"), "postgres must use postgres image"
        # Must have persistent volume
        volumes = pg.get("volumes", [])
        assert len(volumes) > 0, "postgres must have persistent volume"

    def test_terminal_proxy_config(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        tp = data["services"]["terminal-proxy"]
        # Must use host network mode
        assert tp.get("network_mode") == "host", "terminal-proxy must use host network"
        # Must mount docker socket
        volumes = tp.get("volumes", [])
        assert any("/var/run/docker.sock" in str(v) for v in volumes), \
            "terminal-proxy must mount Docker socket"

    def test_compose_config_validates(self):
        """docker compose config validates the file without errors."""
        result = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "config", "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

    def test_platform_network_defined(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        networks = data.get("networks", {})
        assert "platform-net" in networks, "Must define platform-net network"

    def test_postgres_volume_defined(self):
        with open(COMPOSE_FILE) as f:
            data = yaml.safe_load(f)
        volumes = data.get("volumes", {})
        assert "postgres-data" in volumes, "Must define postgres-data volume"
