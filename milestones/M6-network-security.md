# M6: Network Security & Egress Control

| Field | Value |
|-------|-------|
| **Risk** | HIGH |
| **Dependencies** | M4 (running containers to apply rules to) |
| **Blocks** | M7, M8 |
| **Plan sections** | 7.1, 7.2, 7.3, 7.6 |

---

## Objective

Implement network isolation between sandbox containers using explicit proxy +
iptables lockdown with per-project domain allowlists. Three independent layers
enforce security: iptables (kernel), Squid ACLs (application), GCP firewall
(perimeter). Each layer alone is sufficient to block unauthorized traffic.

---

## Why This Is High Risk

- iptables rules must be correct — wrong rules = no internet or complete bypass
- Custom iptables chains must coexist with Docker's own chains without conflicts
- Squid configuration for per-project ACLs with graceful SIGHUP reload
- Host-level iptables from a container requires NET_ADMIN cap (deviation #4 — resolved)
- Squid SIGHUP from Terminal Proxy requires host PID namespace (deviation #5 — resolved: `pid: "host"`)
- IPv6 must be disabled on sandbox networks — iptables is IPv4 only
- tc bandwidth limiting — less common, potential kernel version dependencies
- Rules must be cleaned up on container delete — leaked rules accumulate

---

## Scope

**In scope:**
- Squid installation and configuration on host (systemd service, plain forward proxy)
- squid.conf with include directive for per-project fragments
- Per-project Squid conf fragments + ACL domain files (atomic writes)
- Custom iptables chains (SANDBOX-INPUT, SANDBOX-FORWARD) — one-time setup
- Per-container iptables rules: INPUT ACCEPT (gateway:3128), INPUT DROP, FORWARD DROP
- IPv6 lockdown: `enable_ipv6=False` on networks + global `ip6tables FORWARD DROP`
- tc bandwidth limiting per container veth interface
- Serialized sandbox operations (lock around create/delete)
- Cleanup of all rules/files on container delete
- Python module for managing all the above

**Out of scope:**
- Terminal Proxy WebSocket handling (M7)
- Project Service API endpoints (M8)

---

## Deliverables

```
backend/terminal-proxy/
  services/network_manager.py     # iptables, Squid ACL, tc management
  tests/integration/test_network_security.py

# Host configuration
/etc/squid/squid.conf              # Static base config (include + deny all)
/etc/squid/conf.d/                 # Dynamic per-project fragments
/etc/squid/acls/                   # Dynamic per-project domain allowlists
```

---

## Implementation Tasks

1. Install Squid on host as systemd service (plain forward proxy — no ssl_bump)
2. Write base `squid.conf`:
   ```
   http_port 3128
   include /etc/squid/conf.d/*.conf
   http_access deny all
   ```
3. One-time iptables chain setup (Terminal Proxy runs on startup):
   - Create custom chains: `SANDBOX-INPUT`, `SANDBOX-FORWARD`
   - Insert jumps from INPUT and FORWARD into custom chains (idempotent)
   - Add global `ip6tables -A FORWARD -j DROP`
4. Implement `setup_egress_rules(project_id, container_ip, gateway_ip)`:
   - Write Squid per-project conf fragment (atomic write-then-rename):
     ```
     acl project_{id} src {container_ip}/32
     acl project_{id}_domains dstdomain "/etc/squid/acls/project-{id}.acl"
     http_access allow CONNECT project_{id} project_{id}_domains
     http_access allow project_{id} project_{id}_domains
     http_access deny project_{id}
     ```
   - Write domain ACL file (atomic write-then-rename)
   - Add 3 iptables rules:
     - `SANDBOX-INPUT -s $IP -d $GW -p tcp --dport 3128 -j ACCEPT`
     - `SANDBOX-INPUT -s $IP -j DROP`
     - `SANDBOX-FORWARD -s $IP -j DROP`
   - Send SIGHUP to Squid (read PID from `/var/run/squid.pid`)
5. Implement `update_domain_allowlist(project_id, domains)`:
   - Atomic write to ACL file
   - Send SIGHUP to Squid
6. Implement `setup_bandwidth_limit(project_id, rate_mbit)`:
   - Find veth interface for container
   - Apply tc qdisc rule
7. Implement `remove_egress_rules(project_id, container_ip, gateway_ip)`:
   - Remove 3 iptables rules by exact match (`-D`)
   - Delete Squid conf fragment + ACL file
   - Send SIGHUP to Squid
   - Remove tc qdisc
8. Implement serialization lock around create/delete operations
9. Write all tests

---

## Test Cases

### T6.1: Squid starts and serves proxied requests
**Type**: Infrastructure validation
**Steps**:
1. Start Squid systemd service
2. Make an HTTP request through the proxy explicitly
**Assert**:
- `systemctl status squid` shows active/running
- `curl -x http://localhost:3128 http://example.com` succeeds
- Squid access log shows the request

### T6.2: Container HTTP via explicit proxy
**Type**: Integration (Docker + Squid)
**Steps**:
1. Create container with `HTTP_PROXY` / `HTTPS_PROXY` env vars + `extra_hosts`
2. Apply egress rules (iptables + Squid conf)
3. From inside container: `curl http://example.com` (an allowed domain)
4. Check Squid access log
**Assert**:
- curl uses proxy (respects HTTP_PROXY env var)
- Request appears in Squid access log
- Container sees a response

### T6.3: Container HTTPS via CONNECT method
**Type**: Integration (Docker + Squid)
**Steps**:
1. Same setup as T6.2
2. From inside container: `curl https://api.anthropic.com` (an allowed domain)
**Assert**:
- Request appears in Squid access log as CONNECT
- TLS handshake succeeds (Squid does not decrypt — CONNECT tunnel)
- Container receives a valid HTTPS response

### T6.4: Allowed domain passes through Squid
**Type**: Integration (Docker + Squid)
**Setup**: ACL file contains `api.anthropic.com`, `github.com`
**Steps**:
1. Apply egress rules for container
2. From container: `curl https://api.anthropic.com`
**Assert**:
- Request succeeds (200 or expected response)

### T6.5: Blocked domain rejected by Squid
**Type**: Integration (Docker + Squid)
**Setup**: ACL file contains only `api.anthropic.com`
**Steps**:
1. Apply egress rules for container
2. From container: `curl -x http://host.docker.internal:3128 https://evil-site.com`
**Assert**:
- Request fails with Squid "Access Denied" (HTTP 403)
- Squid log shows TCP_DENIED

### T6.6: Bypass attempt — unset proxy, direct connection
**Type**: Integration (Docker + iptables)
**Steps**:
1. Apply egress rules for container
2. From container: `unset HTTP_PROXY HTTPS_PROXY && curl --noproxy '*' http://example.com`
**Assert**:
- Connection times out (SANDBOX-FORWARD DROP)
- Traffic never leaves the VM

### T6.7: Bypass attempt — raw socket to internet
**Type**: Integration (Docker + iptables)
**Steps**:
1. Apply egress rules for container
2. From container: `python3 -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('93.184.216.34', 80))"`
**Assert**:
- Connection times out or refused
- SANDBOX-FORWARD DROP caught the traffic

### T6.8: Bypass attempt — reach host services (Project Service)
**Type**: Integration (Docker + iptables)
**Steps**:
1. Apply egress rules for container
2. From container: try direct TCP to gateway IP on port 8000
**Assert**:
- Connection dropped (SANDBOX-INPUT rule 2: only 3128 is allowed)
- Cannot reach Project Service, Postgres, or any other host port

### T6.9: Bypass attempt — reach host services via proxy
**Type**: Integration (Docker + Squid)
**Steps**:
1. Apply egress rules for container
2. From container: `curl http://host.docker.internal:8000/projects` (goes through proxy)
**Assert**:
- Squid ACL blocks it: `host.docker.internal` not in domain allowlist
- HTTP 403 from Squid

### T6.10: Container cannot reach other containers
**Type**: Integration (Docker + iptables)
**Steps**:
1. Create container A on `net-proj-a`
2. Create container B on `net-proj-b`
3. From container A, try to reach container B's bridge IP
**Assert**:
- Connection fails (SANDBOX-FORWARD DROP)
- Bridge networks are completely isolated

### T6.11: Container cannot reach Project Service network
**Type**: Integration (Docker)
**Steps**:
1. Create sandbox container on `net-proj-x`
2. From sandbox, try to reach `platform-net` IP
**Assert**:
- Connection fails
- Sandbox is on a separate network with no route to platform-net

### T6.12: Squid SIGHUP picks up new ACL files
**Type**: Integration (Squid)
**Steps**:
1. Squid running with no project ACLs
2. Write new ACL file + conf fragment (atomic write)
3. Send SIGHUP to Squid
4. Verify new domain allowlist is active
**Assert**:
- After SIGHUP, new project's domains are allowed
- Other projects' rules still work

### T6.13: Squid SIGHUP picks up ACL deletions
**Type**: Integration (Squid)
**Steps**:
1. Project ACL file exists and is active
2. Delete ACL file + conf fragment
3. Send SIGHUP to Squid
**Assert**:
- After reload, the deleted project's container traffic is denied
- No errors in Squid log

### T6.14: Squid SIGHUP with bad config keeps old config
**Type**: Integration (Squid)
**Steps**:
1. Working Squid config with project A active
2. Write a syntactically invalid conf fragment for project B
3. Send SIGHUP
**Assert**:
- Squid logs an error but does NOT crash
- Project A's rules still work (old config retained)
- No downtime for any project

### T6.15: Bandwidth limit enforced
**Type**: Integration (Docker + tc)
**Steps**:
1. Create container
2. Apply tc qdisc: 10mbit rate limit
3. Run a bandwidth test from inside the container
**Assert**:
- Measured throughput is approximately 10mbit (within 20% tolerance)
- Without tc rule, throughput is significantly higher

### T6.16: iptables cleanup on container delete
**Type**: Integration (Docker + iptables)
**Steps**:
1. Create container, apply iptables rules (3 rules)
2. Delete container, call `remove_egress_rules()`
3. List iptables rules in custom chains
**Assert**:
- No rules referencing the deleted container's IP remain in SANDBOX-INPUT or SANDBOX-FORWARD
- Custom chains still exist (for other containers)

### T6.17: Squid conf cleanup on container delete
**Type**: Integration (Squid)
**Steps**:
1. Project has ACL file + conf fragment
2. Call `remove_egress_rules()`
3. Check filesystem
**Assert**:
- `/etc/squid/conf.d/project-{id}.conf` does not exist
- `/etc/squid/acls/project-{id}.acl` does not exist
- Squid reloaded successfully (SIGHUP)

### T6.18: Multiple containers with different ACLs simultaneously
**Type**: Integration (Docker + Squid)
**Steps**:
1. Create container A with ACL: `github.com`
2. Create container B with ACL: `pypi.org`
3. From container A: `curl https://github.com` → success
4. From container A: `curl -x http://host.docker.internal:3128 https://pypi.org` → blocked
5. From container B: `curl https://pypi.org` → success
6. From container B: `curl -x http://host.docker.internal:3128 https://github.com` → blocked
**Assert**:
- Each container's ACL is enforced independently
- No cross-contamination

### T6.19: Default domain allowlist for new projects
**Type**: Unit test
**Assert**:
- New projects start with a default ACL containing at minimum:
  - `api.anthropic.com` (Claude API)
  - `storage.googleapis.com` (GCS)
  - `pypi.org` (Python packages)
  - `files.pythonhosted.org` (PyPI downloads)
  - `github.com` (git)
  - `registry.npmjs.org` (npm packages)

### T6.20: IPv6 disabled on sandbox networks
**Type**: Integration (Docker)
**Steps**:
1. Create sandbox network with `enable_ipv6=False`
2. Create container on that network
3. From container, check for IPv6 connectivity
**Assert**:
- No IPv6 address assigned to container
- `ip6tables -L FORWARD` shows global DROP rule

### T6.21: IP-address CONNECT request blocked
**Type**: Integration (Docker + Squid)
**Steps**:
1. Apply egress rules for container
2. From container: `curl -x http://host.docker.internal:3128 https://93.184.216.34`
**Assert**:
- Squid denies (IP doesn't match any `dstdomain` ACL)
- HTTP 403

### T6.22: Atomic write prevents partial config reads
**Type**: Unit test
**Steps**:
1. Call `_atomic_write()` with a large domain list
2. Verify file content matches exactly
**Assert**:
- File written via temp + rename (no partial content possible)
- File permissions are correct

---

## Implementation Notes

### Directory structure

M6 creates `backend/terminal-proxy/` and `backend/terminal-proxy/services/network_manager.py`.
This sets up the directory for M7 (Terminal Proxy WebSocket server) to build on — M7 adds
`proxy.py` and the rest of the Terminal Proxy code to this same directory.

### Testing strategy — Docker VM

Integration tests (T6.1–T6.18, T6.20–T6.21) require Linux-only tools: iptables, ip6tables,
tc, Squid, and Docker-in-Docker. These cannot run on macOS.

**Local testing approach**: Run a privileged Docker container as a "VM" that has:
- Docker daemon (Docker-in-Docker)
- Squid installed and running
- iptables/ip6tables available
- tc available

This container acts as the host VM. The network_manager.py code runs inside it and
creates sandbox containers (Docker-in-Docker). The same setup deploys to GCP VMs later
with no code changes — only the outer container goes away.

**Unit tests** (T6.19, T6.22) run locally on any platform — they test config generation,
atomic writes, and default domain lists with no host dependencies.

### Serialization lock

The serialization lock lives in `network_manager.py` as a `threading.Lock`. The Terminal
Proxy (M7) will call network_manager functions from its asyncio event loop using
`asyncio.to_thread()` or `loop.run_in_executor()`, which naturally bridges the
threading lock to async context.

---

## Accepted Risks (v1)

**DNS tunneling**: Container can encode data in DNS queries via Docker's embedded
DNS (127.0.0.11). This traffic originates from the Docker daemon on the host, not
the container — iptables doesn't see it. DNS tunneling is extremely low bandwidth
(~50 bytes/query), requires the attacker to control a nameserver, and is not a
practical concern for 100 users. Phase 2 mitigation: local DNS resolver (unbound)
that only resolves domains in each project's allowlist.

**Subdomain matching**: Squid's `dstdomain` matches the domain AND all subdomains.
`github.com` in the allowlist also allows `*.github.com`. For the default allowlist
domains this is fine — subdomains are controlled by the same trusted organizations.
API docs should state this clearly.

---

## Acceptance Criteria

- [ ] Squid running on host as systemd service (plain forward proxy, no ssl_bump)
- [ ] Custom iptables chains (SANDBOX-INPUT, SANDBOX-FORWARD) created on startup
- [ ] All 22 test cases pass
- [ ] No container can bypass proxy for HTTP/HTTPS traffic (iptables enforced)
- [ ] No container can reach another container, host services, or internet directly
- [ ] Per-project domain allowlists enforced independently
- [ ] All rules are cleaned up on container delete (zero leaked rules)
- [ ] Squid handles 10 concurrent projects without config errors
- [ ] IPv6 disabled on all sandbox networks + global ip6tables FORWARD DROP
- [ ] GCP firewall does NOT expose port 3128
- [ ] Atomic writes for all Squid config/ACL files
