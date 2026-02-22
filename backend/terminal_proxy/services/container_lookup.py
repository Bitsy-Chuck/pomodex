"""Docker SDK container IP lookup."""

import docker
from docker.errors import NotFound


class ContainerNotRunning(Exception):
    """Raised when the sandbox container is not available."""
    pass


def get_container_ip(project_id: str) -> str:
    """Get the bridge network IP of sandbox-{project_id}.

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

    return ip
