---
title: Royal backup and update - Plan
type: feat
date: 2026-08-29
topic: royal-backup-update
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# Royal backup and update - Plan

## Goal Capsule

- **Objective:** A royal-only cabinet action to take a full system snapshot, restore it on another host, and apply a separate update ZIP after integrity checks and tests — without giving that power to admin.
- **Product authority:** This plan owns backup, restore, ZIP update, and the maintenance screen. Admin park/user/role tools stay as they are.
- **Open blockers:** None. First boot on an empty host still needs one Compose/API start before the cabinet exists; that is an assumption, not a blocker.

## Product Contract

### Summary

Royal opens one admin tab and can download a snapshot, restore from a snapshot (wiping current data after confirmation), or upload a release ZIP. The live system is unchanged until tests on a copy pass; a snapshot is taken automatically before cutover; other users see a maintenance screen for the whole operation.

### Problem Frame

There is no in-app backup or update path. Host docs only say to copy the SQLite volume by hand. A royal who does not want to SSH still cannot move the fleet to another machine or ship a new version.

### Key Decisions

- Only the `royal` role, hardcoded — not a grantable permission. (session-settled: user-directed — chosen over admin access: backup and update are owner-only.) Governs R1, R2.
- Snapshot and release are different archive kinds. (session-settled: user-approved — chosen over one ZIP format: restoring data must not be confused with shipping code.) Governs R3, R4, R8.
- Tests run on a copy; cutover only after they pass. (session-settled: user-approved — chosen over in-place replace: a failed package must leave the live system untouched.) Governs R8, R9.
- Full snapshot plus ZIP update in the first delivery, not a backup-only slice. (session-settled: user-directed — chosen over a smaller MVP.) Governs R3–R11.
- Other users are locked out with a maintenance screen during snapshot, restore, or update. (session-settled: user-directed — chosen over keeping the app usable during ops.) Governs R12, R13.

### Actors

- A1. Royal operator — the signed-in royal who starts the job and watches progress.
- A2. Other signed-in users — including admin and other royals in other sessions.
- A3. Anonymous visitor — login/register during the job.

### Requirements

**Access**

- R1. Snapshot, restore, and update actions are available only to an authenticated user whose role is `royal`.
- R2. Admin (and every other role) cannot see the tab, call the actions, or receive a grant that unlocks them.

**Snapshot**

- R3. A snapshot archive contains the database, on-disk data files, and host/app config files needed to bring the same system up on another machine, plus a manifest that names kind `snapshot`, app version, and checksums.
- R4. Restoring a snapshot on a host that already runs the app replaces current data after the royal types the confirmation phrase. A snapshot cannot be applied as an update.
- R5. After restore, Tracker/Emergency secrets remain readable only if the new host uses the same encryption master key that sealed them; otherwise the royal re-enters those secrets in admin.

**Update**

- R6. An update archive is a different kind (`release`) with a manifest and per-file checksums. Arbitrary ZIPs and snapshot archives are rejected.
- R7. The system checks archive integrity (manifest, checksums, path safety, size limits) before any test or copy.
- R8. Tests run against the unpacked copy. If they fail, the live system is unchanged and the royal sees the log.
- R9. If tests pass, the system takes a snapshot, then switches to the new copy. If the new copy does not become healthy, it rolls back to that snapshot.
- R10. Cutover may briefly interrupt service.

**Cabinet**

- R11. Snapshot download, restore upload, and release upload live on one royal-only admin tab, with live job progress for the operator.

**Maintenance**

- R12. While a snapshot, restore, or update job runs, A2 and A3 cannot use the product; they see a full-screen maintenance state with motion (honouring reduced-motion).
- R13. A1 stays on the ops tab and can watch progress; health probes used by the host supervisor stay up so the process is not killed mid-job.

### Key Flows

- F1. Take snapshot
  - **Trigger:** Royal confirms snapshot on the ops tab.
  - **Actors:** A1, A2
  - **Steps:** Maintenance starts. Snapshot is built. A1 downloads the archive. Maintenance ends.
  - **Covered by:** R1, R3, R11, R12, R13
- F2. Restore on this or another host
  - **Trigger:** Royal uploads a snapshot ZIP and types the restore confirmation.
  - **Actors:** A1, A2
  - **Steps:** Kind must be snapshot. Maintenance starts. Current data is replaced. App comes back on the restored data. Maintenance ends.
  - **Covered by:** R3, R4, R5, R12
- F3. Apply release
  - **Trigger:** Royal uploads a release ZIP and types the update confirmation.
  - **Actors:** A1, A2
  - **Steps:** Integrity check. Tests on a copy. On failure, stop. On success, automatic snapshot, cutover, health check, rollback if unhealthy.
  - **Covered by:** R6–R10, R12, R13

### Acceptance Examples

- AE1. Admin cannot backup
  - **Covers R1, R2.**
  - **Given** an admin session
  - **When** they open admin or call backup/update
  - **Then** the ops tab is absent and the API returns forbidden
- AE2. Failed tests leave the live system
  - **Covers R8.**
  - **Given** a release ZIP whose tests fail
  - **When** royal uploads it
  - **Then** no cutover happens and the current version still serves
- AE3. Snapshot is not an update
  - **Covers R4, R6.**
  - **Given** a valid snapshot ZIP
  - **When** royal uploads it as an update
  - **Then** it is rejected without tests or cutover
- AE4. Others see maintenance, operator does not
  - **Covers R12, R13.**
  - **Given** a running snapshot job started by royal A
  - **When** operator B (or anonymous) uses the app
  - **Then** they get the maintenance screen; royal A still sees job progress; liveness still reports up

### Scope Boundaries

- In: royal tab, two archive kinds, snapshot/restore, release with tests-then-cutover, auto-snapshot, rollback on unhealthy cutover, maintenance screen.
- Deferred: scheduled backups, admin access, git-pull from the UI, a single universal ZIP kind.
- Out: changing Tracker/Emergency product behaviour; multi-host clustering.

### Dependencies / Assumptions

- An empty host still needs one first start (Compose or local API) and a royal login before restore-from-UI is possible.
- Production is the host Docker stack with SQLite on the data volume and config in `host.env`.
- The same encryption master key is required for sealed secrets to survive a restore (R5).

### Outstanding Questions

- Deferred to Planning: how the host process actually swaps containers versus copying files in local dev — product behaviour above stays the same.
- Deferred to Planning: exact confirmation phrases and file size caps.
