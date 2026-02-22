"""
Integration tests for M6: Network Security & Egress Control.

These tests run inside a privileged Docker VM (Docker-in-Docker) with
Squid, iptables, and tc available. They verify real network isolation.

Test IDs map to the milestone doc: T6.1 through T6.22.
"""

import os
import signal
import subprocess
import time

import docker
import pytest

from backend.terminal_proxy.services.network_manager import (
    DEFAULT_DOMAINS,
    SQUID_ACL_DIR,
    SQUID_CONF_DIR,
    atomic_write,
    generate_acl_content,
    generate_squid_conf_fragment,
    reload_squid,
    remove_egress_rules,
    setup_bandwidth_limit,
    setup_chains,
    setup_egress_rules,
    update_domain_allowlist,
)

PROXY_PORT = 3128
TEST_IMAGE = "sandbox-test"
SQUID_CACHE_LOG = "/var/log/squid/cache.log"


def _check_squid_health(label=""):
    """Diagnostic: check if Squid is alive and log state."""
    prefix = f"[DIAG {label}]" if label else "[DIAG]"

    # Check PID files
    from backend.terminal_proxy.services.network_manager import SQUID_PID_FILES
    pid_found = None
    for p in SQUID_PID_FILES:
        if os.path.exists(p):
            try:
                pid_found = int(open(p).read().strip())
            except Exception as e:
                print(f"{prefix} PID file {p} exists but unreadable: {e}")
            break
    if pid_found is None:
        print(f"{prefix} NO PID FILE FOUND in {SQUID_PID_FILES}")
    else:
        print(f"{prefix} PID file found, pid={pid_found}")

    # Check if process is actually running
    ps_result = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True,
    )
    squid_procs = [l for l in ps_result.stdout.split("\n") if "squid" in l.lower() and "grep" not in l]
    print(f"{prefix} Squid processes: {len(squid_procs)}")
    for proc in squid_procs:
        print(f"{prefix}   {proc.strip()}")

    # Check squid -k check
    check_result = subprocess.run(
        ["squid", "-k", "check", "-f", "/etc/squid/squid.conf"],
        capture_output=True, text=True,
    )
    print(f"{prefix} squid -k check: rc={check_result.returncode}")

    # Tail cache.log
    if os.path.exists(SQUID_CACHE_LOG):
        tail = subprocess.run(
            ["tail", "-5", SQUID_CACHE_LOG],
            capture_output=True, text=True,
        )
        print(f"{prefix} cache.log tail:")
        for line in tail.stdout.strip().split("\n"):
            print(f"{prefix}   {line}")

    return pid_found is not None and check_result.returncode == 0


@pytest.fixture(autouse=True)
def squid_health_check(request):
    """Check Squid health before and after every test."""
    test_name = request.node.name
    _check_squid_health(f"BEFORE {test_name}")
    yield
    _check_squid_health(f"AFTER {test_name}")


@pytest.fixture(scope="session")
def docker_client():
    return docker.from_env()


@pytest.fixture(scope="session", autouse=True)
def init_chains():
    """One-time iptables chain setup for the test session."""
    setup_chains()


def _create_sandbox(docker_client, project_id, domains=None):
    """Helper: create a network + container with proxy env vars."""
    network_name = f"net-{project_id}"
    container_name = f"sandbox-{project_id}"

    # Create isolated bridge network (IPv6 disabled)
    network = docker_client.networks.create(
        network_name, driver="bridge", enable_ipv6=False,
    )

    # Run container with proxy vars
    container = docker_client.containers.run(
        TEST_IMAGE,
        command="sleep 3600",
        name=container_name,
        detach=True,
        network=network_name,
        environment={
            "HTTP_PROXY": f"http://host.docker.internal:{PROXY_PORT}",
            "HTTPS_PROXY": f"http://host.docker.internal:{PROXY_PORT}",
            "NO_PROXY": "localhost,127.0.0.1",
        },
        extra_hosts={"host.docker.internal": "host-gateway"},
    )
    container.reload()

    # Get IPs
    net_settings = container.attrs["NetworkSettings"]["Networks"][network_name]
    container_ip = net_settings["IPAddress"]
    gateway_ip = net_settings["Gateway"]

    # Apply egress rules
    if domains is None:
        domains = list(DEFAULT_DOMAINS)
    setup_egress_rules(project_id, container_ip, gateway_ip, domains)

    return container, container_ip, gateway_ip, network


def _cleanup_sandbox(docker_client, project_id, container, container_ip, gateway_ip, network):
    """Helper: remove container, egress rules, network."""
    remove_egress_rules(project_id, container_ip, gateway_ip)
    try:
        container.remove(force=True)
    except Exception:
        pass
    try:
        network.remove()
    except Exception:
        pass


def _exec_in_container(container, cmd, timeout=10):
    """Run a command in a container and return (exit_code, output)."""
    exit_code, output = container.exec_run(
        ["sh", "-c", cmd], demux=False,
    )
    return exit_code, output.decode("utf-8", errors="replace") if output else ""


# ============================================================
# T6.1: Squid starts and serves proxied requests
# ============================================================
class TestT6_1_SquidRunning:
    def test_squid_is_running(self):
        result = subprocess.run(
            ["squid", "-k", "check", "-f", "/etc/squid/squid.conf"],
            capture_output=True,
        )
        assert result.returncode == 0

    def test_proxy_serves_http(self):
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", f"http://localhost:{PROXY_PORT}", "http://example.com"],
            capture_output=True, text=True, timeout=15,
        )
        # Squid may deny (403) since no ACL allows this, but it's responding
        assert result.stdout.strip() in ("200", "403")


# ============================================================
# T6.2: Container HTTP via explicit proxy
# ============================================================
class TestT6_2_ContainerHTTP:
    def test_container_http_via_proxy(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-2", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null -S http://example.com 2>&1 | head -1",
            )
            assert "200" in out or code == 0
        finally:
            _cleanup_sandbox(docker_client, "t6-2", container, cip, gip, net)


# ============================================================
# T6.3: Container HTTPS via CONNECT method
# ============================================================
class TestT6_3_ContainerHTTPS:
    def test_container_https_via_connect(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-3", ["api.anthropic.com"],
        )
        try:
            # wget --spider does a HEAD request over HTTPS via CONNECT tunnel
            code, out = _exec_in_container(
                container,
                "wget --spider -q https://api.anthropic.com 2>&1; echo EXIT=$?",
                timeout=15,
            )
            # Connection through proxy works (may get 401/403 but tunnel is established)
            # wget exit 0 = OK, exit 8 = server error (still means tunnel worked)
            assert "EXIT=0" in out or "EXIT=8" in out
        finally:
            _cleanup_sandbox(docker_client, "t6-3", container, cip, gip, net)


# ============================================================
# T6.4: Allowed domain passes through Squid
# ============================================================
class TestT6_4_AllowedDomain:
    def test_allowed_domain_succeeds(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-4", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://example.com && echo OK",
            )
            assert "OK" in out
        finally:
            _cleanup_sandbox(docker_client, "t6-4", container, cip, gip, net)


# ============================================================
# T6.5: Blocked domain rejected by Squid
# ============================================================
class TestT6_5_BlockedDomain:
    def test_blocked_domain_denied(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-5", ["example.com"],  # only example.com allowed
        )
        try:
            # wget to a non-allowed domain should fail (Squid returns 403)
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://evil-site.com 2>&1; echo EXIT=$?",
                timeout=10,
            )
            # wget exits non-zero when proxy returns 403
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-5", container, cip, gip, net)


# ============================================================
# T6.6: Bypass attempt — unset proxy, direct connection
# ============================================================
class TestT6_6_BypassDirect:
    def test_direct_connection_blocked(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-6", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                container,
                "unset HTTP_PROXY HTTPS_PROXY; "
                "wget -q -T 5 --no-proxy -O /dev/null http://example.com 2>&1 || echo BLOCKED",
                timeout=15,
            )
            assert "BLOCKED" in out
        finally:
            _cleanup_sandbox(docker_client, "t6-6", container, cip, gip, net)


# ============================================================
# T6.7: Bypass attempt — raw socket to internet
# ============================================================
class TestT6_7_BypassRawSocket:
    def test_raw_socket_blocked(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-7", ["example.com"],
        )
        try:
            # Alpine doesn't have python3 by default, use nc
            code, out = _exec_in_container(
                container,
                "nc -w 5 93.184.216.34 80 < /dev/null 2>&1; echo EXIT=$?",
                timeout=15,
            )
            assert "EXIT=0" not in out  # connection should fail
        finally:
            _cleanup_sandbox(docker_client, "t6-7", container, cip, gip, net)


# ============================================================
# T6.8: Bypass attempt — reach host services
# ============================================================
class TestT6_8_BypassHostServices:
    def test_cannot_reach_host_port_8000(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-8", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                container,
                f"nc -w 5 {gip} 8000 < /dev/null 2>&1; echo EXIT=$?",
                timeout=15,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-8", container, cip, gip, net)


# ============================================================
# T6.9: Bypass attempt — reach host services via proxy
# ============================================================
class TestT6_9_BypassHostViaProxy:
    def test_host_services_blocked_via_proxy(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-9", ["example.com"],
        )
        try:
            # Try to reach host services through the proxy — should be denied
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://host.docker.internal:8000/projects 2>&1; echo EXIT=$?",
                timeout=10,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-9", container, cip, gip, net)


# ============================================================
# T6.10: Container cannot reach other containers
# ============================================================
class TestT6_10_ContainerIsolation:
    def test_containers_isolated(self, docker_client):
        ca, cip_a, gip_a, net_a = _create_sandbox(
            docker_client, "t6-10a", ["example.com"],
        )
        cb, cip_b, gip_b, net_b = _create_sandbox(
            docker_client, "t6-10b", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                ca,
                f"nc -w 5 {cip_b} 80 < /dev/null 2>&1; echo EXIT=$?",
                timeout=15,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-10a", ca, cip_a, gip_a, net_a)
            _cleanup_sandbox(docker_client, "t6-10b", cb, cip_b, gip_b, net_b)


# ============================================================
# T6.11: Container cannot reach Project Service network
# ============================================================
class TestT6_11_PlatformNetIsolation:
    def test_cannot_reach_platform_net(self, docker_client):
        # Create a dummy "platform-net" container
        platform_net = docker_client.networks.create("platform-net-test", driver="bridge")
        platform_container = docker_client.containers.run(
            TEST_IMAGE, command="sleep 3600", name="platform-svc-test",
            detach=True, network="platform-net-test",
        )
        platform_container.reload()
        platform_ip = platform_container.attrs["NetworkSettings"]["Networks"]["platform-net-test"]["IPAddress"]

        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-11", ["example.com"],
        )
        try:
            code, out = _exec_in_container(
                container,
                f"nc -w 5 {platform_ip} 80 < /dev/null 2>&1; echo EXIT=$?",
                timeout=15,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-11", container, cip, gip, net)
            platform_container.remove(force=True)
            platform_net.remove()


# ============================================================
# T6.12: Squid SIGHUP picks up new ACL files
# ============================================================
class TestT6_12_SquidSighupAdd:
    def test_sighup_picks_up_new_acl(self, docker_client):
        project_id = "t6-12"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )
        try:
            # Update ACL to add a new domain
            update_domain_allowlist(project_id, ["example.com", "httpbin.org"])

            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://httpbin.org/get && echo OK",
                timeout=10,
            )
            assert "OK" in out
        finally:
            _cleanup_sandbox(docker_client, project_id, container, cip, gip, net)


# ============================================================
# T6.13: Squid SIGHUP picks up ACL deletions
# ============================================================
class TestT6_13_SquidSighupDelete:
    def test_sighup_picks_up_deletion(self, docker_client):
        project_id = "t6-13"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )
        try:
            # First verify it works
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://example.com && echo OK",
            )
            assert "OK" in out

            # Remove the ACL (simulating container delete)
            remove_egress_rules(project_id, cip, gip)

            # Re-add iptables rules only (to keep container reachable to proxy)
            # but without Squid ACL, traffic should be denied
            from backend.terminal_proxy.services.network_manager import _run
            _run(["iptables", "-A", "SANDBOX-INPUT",
                  "-s", cip, "-d", gip,
                  "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"], check=True)

            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://example.com 2>&1; echo EXIT=$?",
                timeout=10,
            )
            # Should fail — Squid denies since ACL was removed
            assert "EXIT=0" not in out

            # Cleanup iptables
            _run(["iptables", "-D", "SANDBOX-INPUT",
                  "-s", cip, "-d", gip,
                  "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"], check=False)
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
            try:
                net.remove()
            except Exception:
                pass


# ============================================================
# T6.14: Squid SIGHUP with bad config keeps old config
# ============================================================
class TestT6_14_SquidBadConfig:
    def test_bad_config_keeps_old(self, docker_client):
        project_id = "t6-14"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )
        try:
            # Write a bad config fragment for another project
            bad_conf = os.path.join(SQUID_CONF_DIR, "project-bad.conf")
            atomic_write(bad_conf, "this is not valid squid config !!!\n")

            # SIGHUP — Squid should log error but keep old config
            try:
                reload_squid()
            except Exception:
                pass
            time.sleep(1)

            # Original project should still work
            code, out = _exec_in_container(
                container,
                "wget -q -O /dev/null http://example.com && echo OK",
            )
            assert "OK" in out

            # Clean up bad config
            os.unlink(bad_conf)
            reload_squid()
        finally:
            _cleanup_sandbox(docker_client, project_id, container, cip, gip, net)


# ============================================================
# T6.15: Bandwidth limit enforced
# ============================================================
class TestT6_15_BandwidthLimit:
    def test_tc_qdisc_applied(self, docker_client):
        project_id = "t6-15"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )
        try:
            setup_bandwidth_limit(project_id, 10)

            # Verify tc qdisc exists on the veth
            from backend.terminal_proxy.services.network_manager import _find_veth
            veth = _find_veth(project_id)
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", veth],
                capture_output=True, text=True,
            )
            assert "tbf" in result.stdout
            assert "rate" in result.stdout
        finally:
            _cleanup_sandbox(docker_client, project_id, container, cip, gip, net)


# ============================================================
# T6.16: iptables cleanup on container delete
# ============================================================
class TestT6_16_IptablesCleanup:
    def test_rules_removed_on_cleanup(self, docker_client):
        project_id = "t6-16"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )

        # Verify rules exist
        result = subprocess.run(
            ["iptables", "-L", "SANDBOX-INPUT", "-n"],
            capture_output=True, text=True,
        )
        assert cip in result.stdout

        # Cleanup
        _cleanup_sandbox(docker_client, project_id, container, cip, gip, net)

        # Verify rules gone
        result = subprocess.run(
            ["iptables", "-L", "SANDBOX-INPUT", "-n"],
            capture_output=True, text=True,
        )
        assert cip not in result.stdout

        result = subprocess.run(
            ["iptables", "-L", "SANDBOX-FORWARD", "-n"],
            capture_output=True, text=True,
        )
        assert cip not in result.stdout


# ============================================================
# T6.17: Squid conf cleanup on container delete
# ============================================================
class TestT6_17_SquidConfCleanup:
    def test_conf_files_removed(self, docker_client):
        project_id = "t6-17"
        container, cip, gip, net = _create_sandbox(
            docker_client, project_id, ["example.com"],
        )

        # Verify files exist
        assert os.path.exists(os.path.join(SQUID_CONF_DIR, f"project-{project_id}.conf"))
        assert os.path.exists(os.path.join(SQUID_ACL_DIR, f"project-{project_id}.acl"))

        # Cleanup
        _cleanup_sandbox(docker_client, project_id, container, cip, gip, net)

        # Verify files gone
        assert not os.path.exists(os.path.join(SQUID_CONF_DIR, f"project-{project_id}.conf"))
        assert not os.path.exists(os.path.join(SQUID_ACL_DIR, f"project-{project_id}.acl"))


# ============================================================
# T6.18: Multiple containers with different ACLs simultaneously
# ============================================================
class TestT6_18_MultipleACLs:
    def test_independent_acl_enforcement(self, docker_client):
        ca, cip_a, gip_a, net_a = _create_sandbox(
            docker_client, "t6-18a", ["example.com"],
        )
        cb, cip_b, gip_b, net_b = _create_sandbox(
            docker_client, "t6-18b", ["httpbin.org"],
        )
        try:
            # A can reach example.com
            code, out = _exec_in_container(ca, "wget -q -O /dev/null http://example.com && echo OK")
            assert "OK" in out

            # A cannot reach httpbin.org
            _, out = _exec_in_container(
                ca,
                "wget -q -O /dev/null http://httpbin.org/get 2>&1; echo EXIT=$?",
                timeout=10,
            )
            assert "EXIT=0" not in out

            # B can reach httpbin.org
            _, out = _exec_in_container(
                cb,
                "wget -q -O /dev/null http://httpbin.org/get && echo OK",
                timeout=10,
            )
            assert "OK" in out

            # B cannot reach example.com
            _, out = _exec_in_container(
                cb,
                "wget -q -O /dev/null http://example.com 2>&1; echo EXIT=$?",
                timeout=10,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-18a", ca, cip_a, gip_a, net_a)
            _cleanup_sandbox(docker_client, "t6-18b", cb, cip_b, gip_b, net_b)


# ============================================================
# T6.20: IPv6 disabled on sandbox networks
# ============================================================
class TestT6_20_IPv6Disabled:
    def test_no_ipv6_on_sandbox_network(self, docker_client):
        network = docker_client.networks.create(
            "net-t6-20", driver="bridge", enable_ipv6=False,
        )
        container = docker_client.containers.run(
            TEST_IMAGE, command="sleep 3600", name="sandbox-t6-20",
            detach=True, network="net-t6-20",
        )
        try:
            container.reload()
            net_settings = container.attrs["NetworkSettings"]["Networks"]["net-t6-20"]
            assert net_settings.get("GlobalIPv6Address", "") == ""

            # Verify global ip6tables rule
            result = subprocess.run(
                ["ip6tables", "-L", "FORWARD", "-n"],
                capture_output=True, text=True,
            )
            assert "DROP" in result.stdout
        finally:
            container.remove(force=True)
            network.remove()


# ============================================================
# T6.21: IP-address CONNECT request blocked
# ============================================================
class TestT6_21_IPAddressBlocked:
    def test_ip_connect_blocked(self, docker_client):
        container, cip, gip, net = _create_sandbox(
            docker_client, "t6-21", ["example.com"],
        )
        try:
            # HTTPS to an IP address should be blocked — dstdomain won't match an IP
            _, out = _exec_in_container(
                container,
                "wget -q -O /dev/null https://93.184.216.34 2>&1; echo EXIT=$?",
                timeout=10,
            )
            assert "EXIT=0" not in out
        finally:
            _cleanup_sandbox(docker_client, "t6-21", container, cip, gip, net)
