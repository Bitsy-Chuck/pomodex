"""
Network security manager for sandbox containers.

Manages iptables rules, Squid ACL configuration, and tc bandwidth
limiting for per-project network isolation and egress control.
"""

import logging
import os
import pathlib
import signal
import subprocess
import tempfile
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()

DEFAULT_DOMAINS = [
    "api.anthropic.com",
    "storage.googleapis.com",
    "pypi.org",
    "files.pythonhosted.org",
    "github.com",
    "registry.npmjs.org",
]

SQUID_CONF_DIR = "/etc/squid/conf.d"
SQUID_ACL_DIR = "/etc/squid/acls"
SQUID_PID_FILES = [
    "/var/run/squid.pid",
    "/run/squid/squid.pid",
    "/run/squid.pid",
]


def atomic_write(path: str, content: str) -> None:
    """Write content to path atomically via temp file + rename."""
    dir_path = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.rename(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def generate_squid_conf_fragment(project_id: str, container_ip: str) -> str:
    """Generate Squid config fragment for a project."""
    return (
        f"acl project_{project_id} src {container_ip}/32\n"
        f'acl project_{project_id}_domains dstdomain "/etc/squid/acls/project-{project_id}.acl"\n'
        f"http_access allow CONNECT project_{project_id} project_{project_id}_domains\n"
        f"http_access allow project_{project_id} project_{project_id}_domains\n"
        f"http_access deny project_{project_id}\n"
    )


def generate_acl_content(domains: list[str]) -> str:
    """Generate ACL file content from a list of domains."""
    return "\n".join(domains) + "\n"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command. Thin wrapper for testability."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def setup_chains() -> None:
    """One-time iptables chain setup. Idempotent.

    Creates SANDBOX-INPUT and SANDBOX-FORWARD custom chains,
    inserts jumps from INPUT and FORWARD, and adds global
    ip6tables FORWARD DROP.
    """
    # Create custom chains (ignore error if already exist)
    _run(["iptables", "-N", "SANDBOX-INPUT"], check=False)
    _run(["iptables", "-N", "SANDBOX-FORWARD"], check=False)

    # Insert jumps (check first to avoid duplicates)
    result = _run(["iptables", "-C", "INPUT", "-j", "SANDBOX-INPUT"], check=False)
    if result.returncode != 0:
        _run(["iptables", "-I", "INPUT", "1", "-j", "SANDBOX-INPUT"], check=True)

    result = _run(["iptables", "-C", "FORWARD", "-j", "SANDBOX-FORWARD"], check=False)
    if result.returncode != 0:
        _run(["iptables", "-I", "FORWARD", "1", "-j", "SANDBOX-FORWARD"], check=True)

    # Global IPv6 forward drop (check first)
    result = _run(["ip6tables", "-C", "FORWARD", "-j", "DROP"], check=False)
    if result.returncode != 0:
        _run(["ip6tables", "-A", "FORWARD", "-j", "DROP"], check=True)


def reload_squid() -> None:
    """Send SIGHUP to Squid to reload configuration."""
    for pid_path in SQUID_PID_FILES:
        if os.path.exists(pid_path):
            pid = int(open(pid_path).read().strip())
            os.kill(pid, signal.SIGHUP)
            return
    raise FileNotFoundError(
        f"Squid PID file not found in any of: {SQUID_PID_FILES}"
    )


def setup_egress_rules(
    project_id: str,
    container_ip: str,
    gateway_ip: str,
    domains: list[str] | None = None,
) -> None:
    """Set up all egress rules for a container.

    Writes Squid conf fragment + ACL file, adds 3 iptables rules,
    and reloads Squid. Serialized with lock.
    """
    if domains is None:
        domains = list(DEFAULT_DOMAINS)

    with _lock:
        # Write ACL file first (Squid conf references it)
        acl_path = os.path.join(SQUID_ACL_DIR, f"project-{project_id}.acl")
        atomic_write(acl_path, generate_acl_content(domains))

        # Write Squid conf fragment
        conf_path = os.path.join(SQUID_CONF_DIR, f"project-{project_id}.conf")
        atomic_write(conf_path, generate_squid_conf_fragment(project_id, container_ip))

        # Add iptables rules
        _run(["iptables", "-A", "SANDBOX-INPUT",
              "-s", container_ip, "-d", gateway_ip,
              "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"], check=True)
        _run(["iptables", "-A", "SANDBOX-INPUT",
              "-s", container_ip, "-j", "DROP"], check=True)
        _run(["iptables", "-A", "SANDBOX-FORWARD",
              "-s", container_ip, "-j", "DROP"], check=True)

        # Reload Squid
        reload_squid()


def update_domain_allowlist(project_id: str, domains: list[str]) -> None:
    """Update a project's domain allowlist and reload Squid."""
    acl_path = os.path.join(SQUID_ACL_DIR, f"project-{project_id}.acl")
    atomic_write(acl_path, generate_acl_content(domains))
    reload_squid()


def _find_veth(project_id: str) -> str:
    """Find the host veth interface for a container."""
    # Get container PID
    result = _run(
        ["docker", "inspect", f"sandbox-{project_id}",
         "--format", "{{.State.Pid}}"],
        check=True,
    )
    pid = result.stdout.strip()

    # Get the ifindex of eth0 inside the container namespace
    result = _run(
        ["nsenter", "-t", pid, "-n", "cat", "/sys/class/net/eth0/iflink"],
        check=True,
    )
    ifindex = result.stdout.strip()

    # Find the matching veth on the host
    result = _run(["ip", "link", "show"], check=True)
    for line in result.stdout.split("\n"):
        if line.startswith(f"{ifindex}:"):
            # Format: "123: vethXXX@if456: ..."
            return line.split(":")[1].strip().split("@")[0]

    raise RuntimeError(f"Could not find veth for container sandbox-{project_id}")


def setup_bandwidth_limit(project_id: str, rate_mbit: int) -> None:
    """Apply tc bandwidth limit on container's veth interface."""
    veth = _find_veth(project_id)
    _run(
        ["tc", "qdisc", "add", "dev", veth, "root",
         "tbf", "rate", f"{rate_mbit}mbit", "burst", "32kbit", "latency", "400ms"],
        check=True,
    )


def _remove_tc(project_id: str) -> None:
    """Remove tc qdisc from container's veth. Ignores errors."""
    try:
        veth = _find_veth(project_id)
        _run(["tc", "qdisc", "del", "dev", veth, "root"], check=False)
    except Exception:
        logger.debug("tc cleanup skipped for %s (container may be gone)", project_id)


def remove_egress_rules(
    project_id: str,
    container_ip: str,
    gateway_ip: str,
) -> None:
    """Remove all egress rules for a container.

    Removes iptables rules, deletes Squid conf + ACL, reloads Squid,
    and removes tc qdisc. Serialized with lock.
    """
    with _lock:
        # Remove iptables rules by exact match (ignore errors if already gone)
        _run(["iptables", "-D", "SANDBOX-INPUT",
              "-s", container_ip, "-d", gateway_ip,
              "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"], check=False)
        _run(["iptables", "-D", "SANDBOX-INPUT",
              "-s", container_ip, "-j", "DROP"], check=False)
        _run(["iptables", "-D", "SANDBOX-FORWARD",
              "-s", container_ip, "-j", "DROP"], check=False)

        # Delete Squid conf + ACL files
        pathlib.Path(os.path.join(SQUID_CONF_DIR, f"project-{project_id}.conf")).unlink(missing_ok=True)
        pathlib.Path(os.path.join(SQUID_ACL_DIR, f"project-{project_id}.acl")).unlink(missing_ok=True)

        # Reload Squid
        reload_squid()

        # Remove tc qdisc
        _remove_tc(project_id)
