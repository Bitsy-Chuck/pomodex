"""Docker SDK container IP lookup."""

import logging

import docker
from docker.errors import NotFound

logger = logging.getLogger(__name__)

PROXY_CONTAINER_NAME = "terminal-proxy"


class ContainerNotRunning(Exception):
    """Raised when the sandbox container is not available."""
    pass


def _ensure_on_network(client: docker.DockerClient, network_name: str) -> None:
    """Ensure terminal-proxy is connected to the given sandbox network."""
    try:
        network = client.networks.get(network_name)
    except NotFound:
        raise ContainerNotRunning(f"Network {network_name} not found")

    network.reload()
    for _, info in network.attrs["Containers"].items():
        if info.get("Name") == PROXY_CONTAINER_NAME:
            return

    proxy = client.containers.get(PROXY_CONTAINER_NAME)
    network.connect(proxy)
    logger.info("[LOOKUP] connected %s to %s", PROXY_CONTAINER_NAME, network_name)


def get_container_ip(project_id: str) -> str:
    """Get the bridge network IP of sandbox-{project_id}.

    Also ensures terminal-proxy is connected to the sandbox network.
    Raises ContainerNotRunning if container doesn't exist, isn't running,
    or isn't attached to the expected network.
    """
    client = docker.from_env()
    container_name = f"sandbox-{project_id}"
    network_name = f"net-{project_id}"

    try:
        container = client.containers.get(container_name)
    except NotFound:
        raise ContainerNotRunning(f"Container {container_name} not found")

    if container.status != "running":
        raise ContainerNotRunning(
            f"Container {container_name} is {container.status}"
        )

    networks = container.attrs["NetworkSettings"]["Networks"]
    if network_name not in networks:
        raise ContainerNotRunning(
            f"Container not on network {network_name}"
        )

    ip = networks[network_name]["IPAddress"]
    if not ip:
        raise ContainerNotRunning(
            f"No IP for {container_name} on {network_name}"
        )

    _ensure_on_network(client, network_name)

    return ip
