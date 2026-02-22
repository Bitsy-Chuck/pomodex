"""Unit tests for network_manager — config generation, atomic writes, defaults."""

import os
import signal
import stat
import subprocess
import tempfile
from unittest.mock import call, patch

import pytest

from backend.terminal_proxy.services.network_manager import (
    DEFAULT_DOMAINS,
    SQUID_PID_FILES,
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


class TestDefaultDomains:
    """T6.19: Default domain allowlist for new projects."""

    def test_contains_claude_api(self):
        assert "api.anthropic.com" in DEFAULT_DOMAINS

    def test_contains_gcs(self):
        assert "storage.googleapis.com" in DEFAULT_DOMAINS

    def test_contains_pypi(self):
        assert "pypi.org" in DEFAULT_DOMAINS

    def test_contains_pypi_downloads(self):
        assert "files.pythonhosted.org" in DEFAULT_DOMAINS

    def test_contains_github(self):
        assert "github.com" in DEFAULT_DOMAINS

    def test_contains_npm(self):
        assert "registry.npmjs.org" in DEFAULT_DOMAINS


class TestAtomicWrite:
    """T6.22: Atomic write prevents partial config reads."""

    def test_writes_content_exactly(self, tmp_path):
        path = str(tmp_path / "test.acl")
        content = "api.anthropic.com\ngithub.com\npypi.org\n"
        atomic_write(path, content)
        assert open(path).read() == content

    def test_writes_large_domain_list(self, tmp_path):
        path = str(tmp_path / "large.acl")
        domains = [f"domain-{i}.example.com" for i in range(1000)]
        content = "\n".join(domains) + "\n"
        atomic_write(path, content)
        assert open(path).read() == content

    def test_file_permissions_are_readable(self, tmp_path):
        path = str(tmp_path / "perms.acl")
        atomic_write(path, "test\n")
        mode = os.stat(path).st_mode
        assert mode & stat.S_IRUSR  # owner readable
        assert mode & stat.S_IWUSR  # owner writable

    def test_overwrites_existing_file(self, tmp_path):
        path = str(tmp_path / "overwrite.acl")
        atomic_write(path, "old content\n")
        atomic_write(path, "new content\n")
        assert open(path).read() == "new content\n"

    def test_no_temp_files_left_on_success(self, tmp_path):
        path = str(tmp_path / "clean.acl")
        atomic_write(path, "content\n")
        files = os.listdir(tmp_path)
        assert files == ["clean.acl"]


class TestGenerateSquidConfFragment:
    """Config fragment generation for per-project Squid rules."""

    def test_contains_src_acl(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        assert "acl project_proj-123 src 172.20.0.2/32" in fragment

    def test_contains_dstdomain_acl(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        assert 'acl project_proj-123_domains dstdomain "/etc/squid/acls/project-proj-123.acl"' in fragment

    def test_contains_connect_allow(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        assert "http_access allow CONNECT project_proj-123 project_proj-123_domains" in fragment

    def test_contains_http_allow(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        assert "http_access allow project_proj-123 project_proj-123_domains" in fragment

    def test_contains_deny_all_else(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        assert "http_access deny project_proj-123" in fragment

    def test_order_is_allow_connect_then_allow_then_deny(self):
        fragment = generate_squid_conf_fragment("proj-123", "172.20.0.2")
        lines = fragment.strip().split("\n")
        # Find the http_access lines
        access_lines = [l.strip() for l in lines if "http_access" in l]
        assert len(access_lines) == 3
        assert "allow CONNECT" in access_lines[0]
        assert "allow project_proj-123 project_proj-123_domains" == access_lines[1].replace("http_access ", "")
        assert "deny" in access_lines[2]


class TestGenerateAclContent:
    """ACL file content generation."""

    def test_generates_one_domain_per_line(self):
        domains = ["api.anthropic.com", "github.com"]
        content = generate_acl_content(domains)
        assert content == "api.anthropic.com\ngithub.com\n"

    def test_empty_domains_produces_empty_with_newline(self):
        content = generate_acl_content([])
        assert content == "\n"

    def test_single_domain(self):
        content = generate_acl_content(["example.com"])
        assert content == "example.com\n"


class TestSetupChains:
    """One-time iptables chain setup (idempotent)."""

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_creates_sandbox_input_chain(self, mock_run):
        setup_chains()
        mock_run.assert_any_call(
            ["iptables", "-N", "SANDBOX-INPUT"], check=False
        )

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_creates_sandbox_forward_chain(self, mock_run):
        setup_chains()
        mock_run.assert_any_call(
            ["iptables", "-N", "SANDBOX-FORWARD"], check=False
        )

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_inserts_jump_to_sandbox_input(self, mock_run):
        # -C check fails (chain jump doesn't exist yet) -> insert it
        mock_run.side_effect = lambda cmd, **kw: (
            subprocess.CompletedProcess(cmd, returncode=1)
            if "-C" in cmd
            else subprocess.CompletedProcess(cmd, returncode=0)
        )
        setup_chains()
        mock_run.assert_any_call(
            ["iptables", "-I", "INPUT", "1", "-j", "SANDBOX-INPUT"], check=True
        )

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_inserts_jump_to_sandbox_forward(self, mock_run):
        mock_run.side_effect = lambda cmd, **kw: (
            subprocess.CompletedProcess(cmd, returncode=1)
            if "-C" in cmd
            else subprocess.CompletedProcess(cmd, returncode=0)
        )
        setup_chains()
        mock_run.assert_any_call(
            ["iptables", "-I", "FORWARD", "1", "-j", "SANDBOX-FORWARD"], check=True
        )

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_skips_insert_if_jump_already_exists(self, mock_run):
        # -C check succeeds (jump already exists) -> don't insert
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        setup_chains()
        insert_calls = [c for c in mock_run.call_args_list if "-I" in c.args[0]]
        assert len(insert_calls) == 0

    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_adds_ipv6_forward_drop(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], returncode=0)
        setup_chains()
        mock_run.assert_any_call(
            ["ip6tables", "-C", "FORWARD", "-j", "DROP"], check=False
        )


class TestSetupEgressRules:
    """setup_egress_rules: iptables + Squid conf + ACL + SIGHUP."""

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_adds_iptables_accept_rule(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        mock_run.assert_any_call(
            ["iptables", "-A", "SANDBOX-INPUT",
             "-s", "172.20.0.2", "-d", "172.20.0.1",
             "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"],
            check=True,
        )

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_adds_iptables_input_drop(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        mock_run.assert_any_call(
            ["iptables", "-A", "SANDBOX-INPUT",
             "-s", "172.20.0.2", "-j", "DROP"],
            check=True,
        )

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_adds_iptables_forward_drop(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        mock_run.assert_any_call(
            ["iptables", "-A", "SANDBOX-FORWARD",
             "-s", "172.20.0.2", "-j", "DROP"],
            check=True,
        )

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_writes_squid_conf_fragment(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        conf_calls = [c for c in mock_write.call_args_list
                      if "conf.d" in c.args[0]]
        assert len(conf_calls) == 1
        assert "project-proj-1.conf" in conf_calls[0].args[0]

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_writes_acl_file(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        acl_calls = [c for c in mock_write.call_args_list
                     if "acls" in c.args[0]]
        assert len(acl_calls) == 1
        assert "project-proj-1.acl" in acl_calls[0].args[0]
        assert "github.com" in acl_calls[0].args[1]

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_reloads_squid(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1", ["github.com"])
        mock_reload.assert_called_once()

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_uses_default_domains_when_none_provided(self, mock_run, mock_write, mock_reload):
        setup_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        acl_calls = [c for c in mock_write.call_args_list if "acls" in c.args[0]]
        content = acl_calls[0].args[1]
        assert "api.anthropic.com" in content
        assert "github.com" in content


class TestUpdateDomainAllowlist:
    """update_domain_allowlist: atomic ACL write + SIGHUP."""

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    def test_writes_acl_file(self, mock_write, mock_reload):
        update_domain_allowlist("proj-1", ["example.com", "test.org"])
        mock_write.assert_called_once()
        assert "project-proj-1.acl" in mock_write.call_args.args[0]
        assert "example.com" in mock_write.call_args.args[1]
        assert "test.org" in mock_write.call_args.args[1]

    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager.atomic_write")
    def test_reloads_squid(self, mock_write, mock_reload):
        update_domain_allowlist("proj-1", ["example.com"])
        mock_reload.assert_called_once()


class TestSetupBandwidthLimit:
    """setup_bandwidth_limit: tc qdisc on container veth."""

    @patch("backend.terminal_proxy.services.network_manager._find_veth")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_applies_tc_qdisc(self, mock_run, mock_veth):
        mock_veth.return_value = "veth123abc"
        setup_bandwidth_limit("proj-1", 10)
        mock_run.assert_any_call(
            ["tc", "qdisc", "add", "dev", "veth123abc", "root",
             "tbf", "rate", "10mbit", "burst", "32kbit", "latency", "400ms"],
            check=True,
        )

    @patch("backend.terminal_proxy.services.network_manager._find_veth")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_uses_correct_rate(self, mock_run, mock_veth):
        mock_veth.return_value = "veth999"
        setup_bandwidth_limit("proj-1", 50)
        tc_calls = [c for c in mock_run.call_args_list if "tc" in c.args[0]]
        assert "50mbit" in tc_calls[0].args[0]


class TestReloadSquid:
    """reload_squid: finds PID file from multiple paths, sends SIGHUP."""

    @patch("backend.terminal_proxy.services.network_manager.os.kill")
    @patch("builtins.open", create=True)
    @patch("backend.terminal_proxy.services.network_manager.os.path.exists")
    def test_uses_first_existing_pid_file(self, mock_exists, mock_open, mock_kill):
        mock_exists.side_effect = lambda p: p == SQUID_PID_FILES[0]
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda s, *a: None
        mock_open.return_value.read.return_value = "42\n"
        reload_squid()
        mock_kill.assert_called_once_with(42, signal.SIGHUP)

    @patch("backend.terminal_proxy.services.network_manager.os.kill")
    @patch("builtins.open", create=True)
    @patch("backend.terminal_proxy.services.network_manager.os.path.exists")
    def test_falls_back_to_second_pid_file(self, mock_exists, mock_open, mock_kill):
        mock_exists.side_effect = lambda p: p == SQUID_PID_FILES[1]
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda s, *a: None
        mock_open.return_value.read.return_value = "99\n"
        reload_squid()
        mock_kill.assert_called_once_with(99, signal.SIGHUP)

    @patch("backend.terminal_proxy.services.network_manager.os.path.exists")
    def test_raises_if_no_pid_file_found(self, mock_exists):
        mock_exists.return_value = False
        with pytest.raises(FileNotFoundError):
            reload_squid()


class TestRemoveEgressRules:
    """remove_egress_rules: cleanup iptables + Squid + tc."""

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_removes_iptables_accept_rule(self, mock_run, mock_reload, mock_tc):
        remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        mock_run.assert_any_call(
            ["iptables", "-D", "SANDBOX-INPUT",
             "-s", "172.20.0.2", "-d", "172.20.0.1",
             "-p", "tcp", "--dport", "3128", "-j", "ACCEPT"],
            check=False,
        )

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_removes_iptables_input_drop(self, mock_run, mock_reload, mock_tc):
        remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        mock_run.assert_any_call(
            ["iptables", "-D", "SANDBOX-INPUT",
             "-s", "172.20.0.2", "-j", "DROP"],
            check=False,
        )

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_removes_iptables_forward_drop(self, mock_run, mock_reload, mock_tc):
        remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        mock_run.assert_any_call(
            ["iptables", "-D", "SANDBOX-FORWARD",
             "-s", "172.20.0.2", "-j", "DROP"],
            check=False,
        )

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_deletes_squid_conf(self, mock_run, mock_reload, mock_tc, tmp_path):
        # Create the files so they can be deleted
        conf = tmp_path / "project-proj-1.conf"
        acl = tmp_path / "project-proj-1.acl"
        conf.write_text("test")
        acl.write_text("test")
        import backend.terminal_proxy.services.network_manager as nm
        orig_conf_dir = nm.SQUID_CONF_DIR
        orig_acl_dir = nm.SQUID_ACL_DIR
        nm.SQUID_CONF_DIR = str(tmp_path)
        nm.SQUID_ACL_DIR = str(tmp_path)
        try:
            remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
            assert not conf.exists()
            assert not acl.exists()
        finally:
            nm.SQUID_CONF_DIR = orig_conf_dir
            nm.SQUID_ACL_DIR = orig_acl_dir

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_reloads_squid(self, mock_run, mock_reload, mock_tc):
        remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        mock_reload.assert_called_once()

    @patch("backend.terminal_proxy.services.network_manager._remove_tc")
    @patch("backend.terminal_proxy.services.network_manager.reload_squid")
    @patch("backend.terminal_proxy.services.network_manager._run")
    def test_removes_tc(self, mock_run, mock_reload, mock_tc):
        remove_egress_rules("proj-1", "172.20.0.2", "172.20.0.1")
        mock_tc.assert_called_once_with("proj-1")
