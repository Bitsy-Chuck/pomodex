# Milestones — Agent Sandbox Platform

## Risk-First Ordering

Milestones are ordered by risk. High-risk infrastructure milestones come first
so we discover blockers early before building dependent features on top.

## Milestone Overview

| # | Milestone | Risk | Dependencies |
|---|-----------|------|--------------|
| M1 | [Sandbox Base Image](./M1-sandbox-base-image.md) | CRITICAL | None |
| M2 | [GCS Storage & Backup](./M2-gcs-storage-and-backup.md) | HIGH | M1 |
| M3 | [GCP IAM Per-Project](./M3-gcp-iam-per-project.md) | HIGH | None |
| M4 | [Container Lifecycle](./M4-container-lifecycle.md) | HIGH | M1 |
| M5 | [Snapshot & Restore](./M5-snapshot-and-restore.md) | HIGH | M4 |
| M6 | [Network Security & Egress](./M6-network-security.md) | HIGH | M4 |
| M7 | [Terminal Proxy](./M7-terminal-proxy.md) | MEDIUM | M4, M6 |
| M8 | [Project Service API](./M8-project-service-api.md) | MEDIUM | M2, M3, M4, M5, M6, M7 |
| M9 | [Web Client](./M9-web-client.md) | LOW | M8 |
| M10 | [Mobile Client](./M10-mobile-client.md) | LOW | M8 |

## Dependency Graph

```
M1 ───┬──→ M2 ─────────────────────────────┐
      │                                     │
      ├──→ M4 ──┬──→ M5 ──────────────────┐│
      │         │                          ││
      │         ├──→ M6 ──→ M7 ──────────┐││
      │         │                         │││
      │         └─────────────────────────┤││
      │                                   │││
M3 ───────────────────────────────────────┤││
                                          ↓↓↓
                                     M8 (API)
                                      │    │
                                      ↓    ↓
                                     M9   M10
```

## Parallel Work Streams

After M1 completes, three streams can run in parallel:

- **Stream A**: M1 → M2 (GCS storage) → feeds into M8
- **Stream B**: M1 → M4 (lifecycle) → M5 (snapshot), M6 (networking) → M7 (proxy) → feeds into M8
- **Stream C**: M3 (GCP IAM) — fully independent, feeds into M8

After M8 completes:
- **Stream D**: M8 → M9 (web) + M10 (mobile) — can run in parallel

## Deviations Found

See [deviations.md](./deviations.md) for issues found during plan analysis that
need to be resolved before or during implementation.
