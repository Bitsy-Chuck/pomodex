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

    # Guard: reject duplicate container names early, before touching any resources
    try:
        client.containers.get(f"sandbox-{project_id}")
        raise ValueError(f"Container sandbox-{project_id} already exists")
    except NotFound:
        pass

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
                        "GCS_PREFIX": str(project_id),
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


TERMINAL_PROXY_CONTAINER = "terminal-proxy"


def connect_proxy_to_network(project_id: str) -> None:
    """Connect the terminal-proxy container to a sandbox's network.

    This allows the proxy to reach ttyd inside the sandbox via bridge IP.
    Idempotent — silently succeeds if already connected.
    """
    client = _get_client()
    network_name = f"net-{project_id}"
    try:
        network = client.networks.get(network_name)
        network.connect(TERMINAL_PROXY_CONTAINER)
    except APIError as e:
        if "already exists" in str(e).lower():
            pass  # Already connected
        else:
            raise


def disconnect_proxy_from_network(project_id: str) -> None:
    """Disconnect the terminal-proxy from a sandbox's network. Idempotent."""
    client = _get_client()
    network_name = f"net-{project_id}"
    try:
        network = client.networks.get(network_name)
        network.disconnect(TERMINAL_PROXY_CONTAINER)
    except NotFound:
        pass  # Network already gone
    except APIError as e:
        if "is not connected" in str(e).lower():
            pass  # Already disconnected
        else:
            raise


def start_container(project_id: str) -> None:
    """Start a stopped container."""
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    container.start()


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


def get_container_ip(project_id: str) -> str:
    """Get the bridge network IP address of a container."""
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    network_name = f"net-{project_id}"
    networks = container.attrs["NetworkSettings"]["Networks"]
    if network_name not in networks:
        raise ValueError(f"Container not connected to network {network_name}")
    return networks[network_name]["IPAddress"]


def stop_container(project_id: str, timeout: int = 30) -> None:
    """Gracefully stop a running container.

    Sends SIGTERM and waits up to timeout seconds before SIGKILL.
    """
    client = _get_client()
    container = client.containers.get(f"sandbox-{project_id}")
    container.stop(timeout=timeout)


def cleanup_project_resources(project_id: str) -> None:
    """Remove all Docker resources for a project: container, volume, network.

    Idempotent — safe to call even if some or all resources are already gone.
    """
    delete_container(project_id)
    delete_volume(project_id)
    delete_network(project_id)
