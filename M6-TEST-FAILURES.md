# M6 Integration Test Failures — 2026-02-22

**Result**: 11 passed, 10 failed (out of 21 integration tests)
**Unit tests**: All pass (T6.19, T6.22 — run locally)

---

## Passing Tests (11)

- T6.1: Squid starts and serves proxied requests (2 tests)
- T6.2: Container HTTP via explicit proxy
- T6.3: Container HTTPS via CONNECT method
- T6.4: Allowed domain passes through Squid
- T6.7: Bypass attempt — raw socket blocked
- T6.8: Bypass attempt — reach host services (direct TCP)
- T6.10: Container isolation (can't reach other containers)
- T6.11: Platform net isolation
- T6.12: Squid SIGHUP picks up new ACL files
- T6.20: IPv6 disabled on sandbox networks

---

## Failing Tests (10) — 3 Root Causes

### Root Cause 1: Squid not blocking non-allowed traffic (3 tests)

**T6.5: test_blocked_domain_denied** — ASSERTION FAIL
- `assert "EXIT=0" not in out` but got `EXIT=0`
- wget to `evil-site.com` succeeded through proxy even though only `example.com` is allowed
- Likely cause: Squid's `dstdomain` ACL might not match properly, or the deny-all at
  the end of squid.conf fires after the per-project allow but the request gets through
  somehow. Need to investigate Squid access log to see if it's TCP_ALLOWED or TCP_DENIED.

**T6.6: test_direct_connection_blocked** — ASSERTION FAIL
- `assert "BLOCKED" in out` but got empty string
- `wget --no-proxy` to `example.com` didn't get "BLOCKED" appended
- The iptables SANDBOX-FORWARD DROP should block direct outbound, but wget seems to
  succeed (exits 0 so `|| echo BLOCKED` doesn't fire)
- Possible cause: iptables FORWARD chain ordering issue — Docker's own FORWARD rules
  may be evaluated before SANDBOX-FORWARD

**T6.9: test_host_services_blocked_via_proxy** — ASSERTION FAIL
- `assert "EXIT=0" not in out` but got `EXIT=0`
- wget to `http://host.docker.internal:8000/projects` via proxy succeeded
- Squid should deny since `host.docker.internal` isn't in the ACL, but it seems
  the request gets through. Possible cause: `host.docker.internal` resolves to
  127.0.0.1 or gateway IP inside the testvm, and Squid doesn't match the hostname
  properly when the destination is an internal address.

### Root Cause 2: Bad config reload crashes Squid (1 test, causes cascade)

**T6.14: test_bad_config_keeps_old** — FileNotFoundError
- Writes invalid squid config fragment, sends SIGHUP
- Expected: Squid logs error but keeps old config
- Actual: Squid crashes/exits — PID file disappears
- Alpine Squid may not handle SIGHUP gracefully with invalid included config
- Fix option A: Validate config before reload (`squid -k parse -f /etc/squid/squid.conf`)
- Fix option B: Restart Squid if it dies after SIGHUP
- Fix option C: Both — validate first, but also have restart recovery

### Root Cause 3: Cascade — Squid dead from T6.14 onward (6 tests)

All these tests call `setup_egress_rules()` → `reload_squid()` which raises
`FileNotFoundError` because Squid's PID file is gone after T6.14 killed it.

- **T6.13**: test_sighup_picks_up_deletion — assertion fail (domain still allowed after ACL removal)
- **T6.15**: test_tc_qdisc_applied — FileNotFoundError
- **T6.16**: test_rules_removed_on_cleanup — FileNotFoundError
- **T6.17**: test_conf_files_removed — FileNotFoundError
- **T6.18**: test_independent_acl_enforcement — FileNotFoundError
- **T6.21**: test_ip_connect_blocked — FileNotFoundError

Note: T6.13 runs before T6.14 in file order but may still be affected by Squid state.
The assertion error there (`EXIT=0` after ACL removal) suggests Squid isn't re-evaluating
the config after SIGHUP — needs a sleep/wait for reload to take effect.

---

## Fix Priority

1. **Fix T6.14 first** — validate config before SIGHUP + restart recovery.
   This unblocks 6 cascade tests.
2. **Fix T6.5/T6.9** — investigate why Squid allows non-allowed domains.
   Check Squid access.log inside testvm. May need squid.conf changes.
3. **Fix T6.6** — investigate iptables FORWARD chain ordering vs Docker.
4. **Fix T6.13** — add sleep after SIGHUP for config to reload.

---

## What M6 Blocks (beyond network restrictions)

M6 scope is purely network security:
- Egress control (domain allowlists via Squid proxy)
- iptables lockdown (no direct outbound, no host service access)
- Container isolation (no cross-container traffic)
- IPv6 blocking
- Bandwidth limiting (tc)

It does NOT block:
- Filesystem access (that's handled by Docker container isolation / M1 base image)
- Process/syscall restrictions (no seccomp/AppArmor in scope)
- Resource limits (CPU/memory — that's Docker container config in M4)
- DNS tunneling (accepted risk for v1, noted in milestone doc)
