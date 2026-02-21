# Deviations & Issues Found in plan.md

Issues discovered during milestone breakdown. Open items need a decision before
implementation.

---

## OPEN — Decisions Needed

### 6. Capabilities Not Dropped Before Adding SYS_ADMIN

**Location**: Section 5.1 (create code) vs Section 13 (security notes)

**Issue**: Security notes say `--cap-drop=ALL --cap-add=SYS_ADMIN` but create code
only uses `cap_add=["SYS_ADMIN"]` without dropping all caps first.

**Proposed approach**: Update security notes to be honest — caps are additive only
for phase 1. Full `cap_drop=ALL` is a phase 2 hardening task (gcsfuse + SYS_ADMIN
interaction is fiddly to get right without breaking things).

**Status**: Awaiting decision.

---

### 7. Docker Socket = Effective Root Access

**Location**: Section 8 (docker-compose.yml), Section 13 (security notes)

**Issue**: Both project-service and terminal-proxy mount `/var/run/docker.sock`,
giving them effective root on the host. Not mentioned in security notes.

**Proposed approach**: Add to Section 13 as known risk. Both services are trusted
in phase 1. Phase 2 mitigation: replace Docker socket with VM-level API or use
a Docker socket proxy (Tecnativa/docker-socket-proxy) to limit API surface.

**Status**: Awaiting decision.
