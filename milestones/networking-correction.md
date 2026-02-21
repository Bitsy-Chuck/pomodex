# Networking Correction: Explicit Proxy Replaces Transparent Interception

This document replaces the DNAT-based transparent interception approach in
plan.md sections 7.2–7.3. The original approach is broken for HTTPS.

---

## Problem with the Original Plan

The plan uses iptables DNAT to transparently redirect container port 80/443
traffic to Squid:

```bash
iptables -t nat -A PREROUTING -s $CONTAINER_IP -p tcp --dport 443 -j DNAT --to $SQUID_IP:3128
```

This doesn't work for HTTPS. When a container connects to `github.com:443`,
DNAT rewrites the destination to Squid. But the container sends a raw TLS
ClientHello — Squid receives TLS bytes on its plain HTTP port and doesn't
know what to do with them. Connection fails.

Fixing this requires `ssl_bump` configuration with a CA certificate (MITM).
That's complex and unnecessary.

---

## Security Goals

1. Each user configures what their sandbox can access (per-sandbox allowlist)
2. No one inside the sandbox can override or bypass the rules
3. No traffic that isn't in the allowlist should pass
4. Editing one sandbox's allowlist must not affect other sandboxes

These are enforced by three independent layers. Each layer alone is sufficient
to block unauthorized traffic. All three together provide defense in depth.

---

## Corrected Approach: Explicit Proxy + iptables Lockdown

### How It Works

1. Each container gets `HTTP_PROXY` / `HTTPS_PROXY` env vars pointing at Squid
2. Tools (pip, npm, curl, git, Claude API) see the proxy env vars and send
   requests to Squid using the HTTP CONNECT method
3. Squid reads the domain from the CONNECT request, checks the per-project ACL,
   allows or blocks
4. If allowed, Squid creates a TCP tunnel — TLS passes through untouched, no
   MITM, no certificates
5. iptables locks down the container to ONLY reach Squid — nothing else on the
   host, nothing on the internet

### Container Creation

```python
container = docker_client.containers.run(
    image=base_image,
    name=f"sandbox-{project_id}",
    detach=True,
    volumes={f"vol-{project_id}": {"bind": "/home/agent", "mode": "rw"}},
    ports={"22/tcp": ssh_port},
    environment={
        "PROJECT_ID":     str(project_id),
        "GCS_BUCKET":     GCS_BUCKET,
        "GCS_PREFIX":     f"projects/{project_id}",
        "GCS_SA_KEY":     sa_key,
        "SSH_PUBLIC_KEY": public_key,
        "HTTP_PROXY":     "http://host.docker.internal:3128",
        "HTTPS_PROXY":    "http://host.docker.internal:3128",
        "NO_PROXY":       "localhost,127.0.0.1",
    },
    extra_hosts={"host.docker.internal": "host-gateway"},
    network=f"net-{project_id}",
    cap_add=["SYS_ADMIN"],
    devices=["/dev/fuse"],
    security_opt=["apparmor:unconfined"],
    mem_limit="1g",
    nano_cpus=1_000_000_000,
)
```

Key additions vs the original plan:
- `extra_hosts={"host.docker.internal": "host-gateway"}` — maps `host.docker.internal`
  to the host's bridge gateway IP, so the container can reach Squid on the host
  from a custom bridge network
- `HTTP_PROXY` / `HTTPS_PROXY` — tells tools to send requests through Squid
- `NO_PROXY` — skip proxy for localhost connections inside the container

---

## Layer 1: iptables — Kernel-Level Enforcement

iptables rules run in the Linux kernel. The container cannot modify, bypass, or
even see them. This is the hard wall.

### Custom Chains

Docker manages its own iptables rules in FORWARD (DOCKER, DOCKER-USER,
DOCKER-ISOLATION chains). Inserting raw rules with `-I FORWARD 1` is fragile —
Docker can clobber them on daemon restart or network recreation.

Custom chains solve this. Docker doesn't touch chains it didn't create.

**One-time setup** (Terminal Proxy runs this on startup):

```bash
# Create custom chains (idempotent — errors ignored if they already exist)
iptables -N SANDBOX-INPUT  2>/dev/null || true
iptables -N SANDBOX-FORWARD 2>/dev/null || true

# Jump into our chains from the main chains (check before inserting to avoid duplicates)
iptables -C INPUT -j SANDBOX-INPUT 2>/dev/null || iptables -I INPUT 1 -j SANDBOX-INPUT
iptables -C FORWARD -j SANDBOX-FORWARD 2>/dev/null || iptables -I FORWARD 1 -j SANDBOX-FORWARD
```

### Per-Container Rules (3 rules each)

```bash
CONTAINER_IP=$(docker inspect sandbox-{project_id} \
    --format '{{.NetworkSettings.Networks.net-{project_id}.IPAddress}}')
GATEWAY_IP=$(docker inspect sandbox-{project_id} \
    --format '{{.NetworkSettings.Networks.net-{project_id}.Gateway}}')

# Rule 1: Allow container → host gateway on TCP port 3128 ONLY (Squid)
iptables -A SANDBOX-INPUT -s $CONTAINER_IP -d $GATEWAY_IP -p tcp --dport 3128 -j ACCEPT

# Rule 2: Block container → host on ANY other port (Project Service, Postgres, SSH, etc.)
iptables -A SANDBOX-INPUT -s $CONTAINER_IP -j DROP

# Rule 3: Block container → internet directly (bypass attempts)
iptables -A SANDBOX-FORWARD -s $CONTAINER_IP -j DROP
```

### What Each Rule Blocks

| Traffic | Chain | Rule | Result |
|---------|-------|------|--------|
| Container → Squid (gateway:3128) | INPUT | Rule 1 ACCEPT | Allowed |
| Container → Project Service (host:8000) | INPUT | Rule 2 DROP | Blocked |
| Container → PostgreSQL (host:5432) | INPUT | Rule 2 DROP | Blocked |
| Container → SSH (host:22) | INPUT | Rule 2 DROP | Blocked |
| Container → any host port except 3128 | INPUT | Rule 2 DROP | Blocked |
| Container → internet (any IP) | FORWARD | Rule 3 DROP | Blocked |
| Container → other container | FORWARD | Rule 3 DROP | Blocked |
| Container → localhost (inside container) | None | N/A | Works (never hits iptables) |

### Why It Can't Be Bypassed from Inside

- `unset HTTP_PROXY` → tools try direct connections → FORWARD DROP
- Raw socket code → FORWARD DROP
- Discovers host public IP, tries port 8000 → INPUT DROP (not gateway:3128)
- Tries another bridge's gateway → routes through FORWARD → DROP
- ICMP (ping) → INPUT DROP (rule 1 only allows TCP/3128)
- UDP → INPUT DROP
- DNS → Docker's embedded DNS (127.0.0.11) is inside the container namespace,
  never hits iptables. Squid does DNS resolution for CONNECT requests.

### Cleanup on Container Delete

```bash
iptables -D SANDBOX-INPUT -s $CONTAINER_IP -d $GATEWAY_IP -p tcp --dport 3128 -j ACCEPT
iptables -D SANDBOX-INPUT -s $CONTAINER_IP -j DROP
iptables -D SANDBOX-FORWARD -s $CONTAINER_IP -j DROP
```

Deletes by exact match. Only affects this container's IP. Other containers'
rules are untouched.

---

## Layer 2: Squid ACLs — Application-Level Domain Filtering

Even if iptables were somehow bypassed, Squid independently enforces per-project
domain allowlists. This is the layer the user controls through the API.

### Squid Installation

Squid runs on the host as a systemd service. Plain forward proxy — no ssl_bump,
no CA certificates.

### Base Config (`/etc/squid/squid.conf`)

```
http_port 3128
include /etc/squid/conf.d/*.conf
http_access deny all
```

The `include` loads all per-project fragments before the final `deny all`.
Processing order:
1. Per-project rules fire first (scoped by source IP)
2. `deny all` catches any traffic not matched by project rules

### Per-Project Fragment (`/etc/squid/conf.d/project-{id}.conf`)

Written by Terminal Proxy on container create, deleted on container delete.

```
acl project_{id} src {container_ip}/32
acl project_{id}_domains dstdomain "/etc/squid/acls/project-{id}.acl"
http_access allow CONNECT project_{id} project_{id}_domains
http_access allow project_{id} project_{id}_domains
http_access deny project_{id}
```

How this works:
- `acl project_{id} src` — matches traffic from this container's IP only
- `allow CONNECT ... project_{id}_domains` — allows HTTPS tunneling to allowed domains
- `allow ... project_{id}_domains` — allows HTTP to allowed domains
- `deny project_{id}` — blocks everything else from this container

### Why One Project Can't Affect Another

Each `acl project_N src` matches a single IP (`/32`). Project 42's rules only
fire for traffic from 172.18.0.2. Project 43 (172.19.0.2) hits a completely
different ACL chain. The rules are independent — like separate if-else branches
that can never overlap because each container has a unique IP.

### Per-Project Domain Allowlist (`/etc/squid/acls/project-{id}.acl`)

Default allowlist on project creation. User can modify via API.

```
api.anthropic.com
storage.googleapis.com
pypi.org
files.pythonhosted.org
github.com
registry.npmjs.org
```

Example — two sandboxes with different allowlists:

```
# Sandbox A (172.18.0.2) — Python data science project
api.anthropic.com
pypi.org
files.pythonhosted.org
github.com
huggingface.co

# Sandbox B (172.19.0.2) — Node.js web project
api.anthropic.com
registry.npmjs.org
github.com
cdn.jsdelivr.net
```

Sandbox A can reach PyPI + HuggingFace but not npm. Sandbox B can reach npm +
jsDelivr but not PyPI.

### Squid Reload: Safety Guarantees

When Terminal Proxy sends SIGHUP to Squid:
- Squid re-reads ALL config files
- If the new config is valid → applies immediately, existing TCP tunnels
  continue uninterrupted, new rules apply to new connections only
- If the new config has a syntax error → Squid **keeps the old config** and
  logs an error. No crash. No downtime for any project.
- Other projects' ACL files weren't modified, so their rules are identical
  before and after

This means a bad edit to project 42's allowlist cannot bring down Squid or
affect project 43. Worst case: project 42's new rules don't take effect and
Squid keeps the previous working config.

---

## Layer 3: GCP Firewall — Perimeter Defense

Port 3128 must **never** be opened in GCP firewall rules. Even though Squid
binds on `0.0.0.0:3128`, no traffic from the public internet can reach it. Only
traffic from Docker bridge networks (inside the VM) reaches Squid.

This prevents external attackers from using Squid as an open proxy, even without
the iptables INPUT rules.

GCP firewall rules should only open:
- Port 22 (SSH for admin access)
- Port 8000 (Project Service API for web/mobile clients)
- Mapped SSH ports for sandbox access (dynamic range, e.g., 10000–11000)

---

## Terminal Proxy: SIGHUP and docker-compose Config

Terminal Proxy needs to signal Squid (host process) via SIGHUP. This requires
sharing the host's PID namespace — without it, `os.kill()` can't see host PIDs.

```yaml
terminal-proxy:
  network_mode: host
  pid: "host"
  cap_add: [NET_ADMIN]
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
    - /etc/squid/acls:/etc/squid/acls
    - /etc/squid/conf.d:/etc/squid/conf.d
    - /var/run/squid.pid:/var/run/squid.pid:ro
```

- `pid: "host"` — shares host PID namespace so `os.kill(squid_pid, SIGHUP)` works
- `network_mode: host` — shares host network namespace for iptables management
- `cap_add: [NET_ADMIN]` — allows iptables modifications
- Volume mounts — write Squid config/ACL files, read Squid PID

Security note: `pid: "host"` means Terminal Proxy can see/signal all host
processes. Marginal additional exposure — it already has the Docker socket,
which gives effective root access. Both are phase 2 hardening targets.

Reload function:

```python
import os, signal

def reload_squid():
    pid = int(open("/var/run/squid.pid").read().strip())
    os.kill(pid, signal.SIGHUP)
```

---

## Complete Request Flows

### Allowed request (pip install)

```
Container: pip install requests
  |
  +-- pip reads HTTPS_PROXY=http://host.docker.internal:3128
  +-- pip connects to 172.18.0.1:3128 (TCP)
  |
  +-- iptables INPUT chain:
  |     SANDBOX-INPUT: src=172.18.0.2, dst=172.18.0.1, tcp/3128 --> ACCEPT
  |
  +-- Squid receives: CONNECT pypi.org:443
  |     ACL: src 172.18.0.2 --> project_42 --> "pypi.org" in allowlist --> ALLOW
  |
  +-- Squid creates TCP tunnel to pypi.org:443
       TLS handshake between pip and pypi.org (Squid never decrypts)
       pip downloads package. Done.
```

### Blocked domain (not in allowlist)

```
Container: curl https://evil.com
  |
  +-- curl reads HTTPS_PROXY, connects to Squid
  +-- iptables INPUT: src=container, dst=gateway, tcp/3128 --> ACCEPT
  |
  +-- Squid receives: CONNECT evil.com:443
  |     ACL: "evil.com" NOT in allowlist --> DENY
  |
  +-- Squid returns HTTP 403 "Access Denied"
       curl sees error. Connection never reaches evil.com.
```

### Bypass attempt (raw socket, no proxy)

```
Container: python -c "import socket; s=socket.connect(('evil.com', 443))"
  |
  +-- No proxy env var used, direct TCP connection
  +-- Docker embedded DNS resolves evil.com --> 93.184.216.34
  +-- TCP SYN to 93.184.216.34:443
  |
  +-- iptables FORWARD chain:
  |     SANDBOX-FORWARD: src=172.18.0.2 --> DROP
  |
  +-- Packet never leaves the VM. Connection times out.
```

### Attempt to reach host services

```
Container: curl http://host.docker.internal:8000/projects
  |
  +-- curl reads HTTP_PROXY, but host.docker.internal is not in NO_PROXY
  +-- curl sends request through Squid: "GET http://host.docker.internal:8000/..."
  +-- Squid ACL: "host.docker.internal" NOT in domain allowlist --> DENY
  +-- HTTP 403. Blocked by Squid.

Container: python -c "urllib.request.urlopen('http://172.18.0.1:8000')"
  |                    (bypasses proxy, hardcodes gateway IP)
  +-- Direct TCP to 172.18.0.1:8000
  +-- iptables INPUT chain:
  |     SANDBOX-INPUT rule 1: dst=172.18.0.1, tcp/3128? No, port 8000 --> no match
  |     SANDBOX-INPUT rule 2: src=172.18.0.2 --> DROP
  |
  +-- Blocked. Cannot reach Project Service.
```

---

## API Endpoint

```
GET  /projects/{id}/allowed-domains    --> { "domains": ["github.com", ...] }
PUT  /projects/{id}/allowed-domains    --> { "domains": ["github.com", ...] }
```

PUT handler:
1. Validates domain list (non-empty, valid hostnames)
2. Writes `/etc/squid/acls/project-{id}.acl`
3. Sends SIGHUP to Squid
4. Returns updated list

Default domains are set on project creation. User modifies via API at any time.

---

## What Works

Every standard dev tool respects `HTTP_PROXY` / `HTTPS_PROXY`:
- pip / pip3
- npm / yarn / pnpm
- curl / wget
- git (HTTPS)
- Python requests, httpx, urllib
- Node.js fetch, axios, got
- Claude Code CLI (Anthropic SDK)

These all send CONNECT requests to Squid for HTTPS. Squid reads the domain,
checks the ACL, tunnels or blocks. TLS is end-to-end between the tool and the
destination — Squid never decrypts anything.

## What Gets Blocked

Tools that ignore proxy env vars and connect directly:
- Raw socket code
- Some niche binaries
- git over SSH (`git@github.com:...`)

These hit the iptables FORWARD DROP rule and fail. For the sandbox use case
(Claude Code agent doing standard development), this is fine. If git-over-SSH
is needed, the user can use `git clone https://...` instead.

---

## What Changed from the Original Plan

| Aspect | Original (broken) | Corrected |
|--------|--------------------|-----------|
| Traffic routing | iptables DNAT (transparent) | HTTP_PROXY env vars (explicit) |
| HTTPS handling | Doesn't work without ssl_bump | Works via CONNECT method |
| iptables structure | Raw rules in FORWARD | Custom chains (SANDBOX-INPUT, SANDBOX-FORWARD) |
| iptables rules per container | 3 (DNAT x2 + DROP) | 3 (INPUT ACCEPT + INPUT DROP + FORWARD DROP) |
| Host service access | Not restricted (container can reach any host port) | Locked down (only gateway:3128 allowed) |
| Container → host | Assumed 172.17.0.1 reachable | Uses `host.docker.internal` with `host-gateway` |
| Squid config | Needs ssl_bump for HTTPS | Plain forward proxy, no ssl_bump |
| Terminal Proxy PID namespace | Not shared (SIGHUP broken) | `pid: "host"` (SIGHUP works) |
| GCP firewall | Not mentioned | Port 3128 must NOT be opened |

---

## Regression on Existing Sandboxes

**None.** When a new sandbox is created:
- 3 new iptables rules appended to custom chains for the new container IP only —
  does not affect existing rules (append, not insert)
- New Squid conf fragment + ACL file written — only adds rules for the new
  project's source IP
- Squid SIGHUP reload is graceful — existing TCP tunnels continue uninterrupted,
  new config applies to new connections only
- If SIGHUP hits a bad config — Squid keeps old config, no downtime for anyone
- Proxy env vars are per-container — no shared state between sandboxes

When a sandbox is deleted:
- 3 iptables rules deleted by exact match — only removes this container's rules
- Squid conf fragment + ACL file deleted — only removes this project's rules
- Squid SIGHUP reloads cleanly — other projects' rules still intact
