# V1 Improvements (Post-M1 Review)

Date: February 21, 2026
Scope: `M1-sandbox-base-image`

## Goal

Track hardening items found after M1 completion without breaking the current happy-path behavior (image builds, container boots, `sshd`/`ttyd`/`backup-daemon` run under `supervisord`).

## Priority Findings

1. Critical: backup daemon cannot read GCS key file.
- Current behavior:
  - `/tmp/gcs-key.json` is created as `root:root` with mode `600`.
  - `backup-daemon` runs as user `agent`.
  - Result: daemon cannot read credentials.
- Evidence:
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/entrypoint.sh:10`
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/config/supervisord.conf:30`

2. High: restore creates root-owned files in `/home/agent`.
- Current behavior:
  - restore `rclone sync` runs in entrypoint as root.
  - Restored files become `root:root`.
  - Result: agent may lose write access to restored content.
- Evidence:
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/entrypoint.sh:39`

3. High: first-boot restore can abort startup on sync failure.
- Current behavior:
  - `set -e` is enabled.
  - If backup exists and `rclone sync` fails, entrypoint exits before `supervisord` starts.
  - Result: container may fail to start on transient storage/network errors.
- Evidence:
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/entrypoint.sh:2`
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/entrypoint.sh:39`

4. Medium: M1 tests do not fully assert milestone requirements.
- Missing/partial checks:
  - T1.10: no second-start verification that restore is skipped.
  - T1.13: no forced unhealthy transition test after killing `ttyd`.
  - T1.4: no real websocket upgrade validation on `/ws`.
  - T1.9: no assert for `GOOGLE_APPLICATION_CREDENTIALS`.
- Evidence:
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/tests/test_m1_sandbox.sh:370`
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/tests/test_m1_sandbox.sh:455`

5. Medium: build reproducibility drift risk from unpinned installs.
- Current behavior:
  - `ttyd` uses release `latest`.
  - `rclone` installed via remote script.
  - Claude CLI unpinned.
- Evidence:
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/Dockerfile:20`
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/Dockerfile:33`
  - `.worktrees/m1-sandbox-base-image/backend/sandbox/Dockerfile:36`

## Safe Fix Plan (Happy-Path Preserving)

1. Credentials readability (no behavior change for existing success path).
- After writing key file, set ownership to `agent:agent` with mode `600`.
- Keep path unchanged (`/tmp/gcs-key.json`) to avoid downstream changes.

2. Restore ownership normalization.
- After successful restore, run `chown -R agent:agent /home/agent`.
- Keep restore location and first-boot flag logic unchanged.

3. Fail-graceful restore.
- Wrap restore sync with error handling so failures are logged but do not prevent `supervisord` startup.
- Keep existing success behavior identical when restore succeeds.

4. Test tightening (incremental).
- Add assertions only; do not change runtime defaults:
  - verify second start skip log for T1.10,
  - assert unhealthy transition in T1.13,
  - assert `GOOGLE_APPLICATION_CREDENTIALS` in T1.9,
  - add websocket probe in T1.4.

5. Pinning strategy (controlled rollout).
- Introduce version args/envs in Dockerfile with explicit defaults.
- Keep defaults at currently known-good versions to preserve build behavior.

## Risk Note

These are hardening changes; they should not break the happy flow if applied as above because they preserve interfaces, env vars, process model, and startup sequence.
