# Operational reliability implementation plan

**Goal:** implement the six improvements approved by the user: collaboration
safety, reliable submissions, host status, shift handoff, report/photo drafts,
and diagnostic-rule validation against collected samples.

**Architecture:** extend existing FastAPI/SQLite and React flows. Preserve
per-request RBAC, shared caches, 200-user polling budget and existing task tabs.
Use scoped small modules; no new external service or public deployment.

## Tracker collaboration, submissions and handoff

- [x] Add short-lived presence scoped to task and authorized users. Advisory,
  not a permanent task lock; hidden tabs stop presence updates.
- [x] Detect changed status/assignee before mutations and require review of
  fresh state. Preserve drafts on conflicts and denials.
- [x] Persist submission keys/results so duplicate clicks/retries do not repeat
  writes. Unknown upstream outcomes remain uncertain and are never blindly
  replayed. Scope keys by actor, task, action and payload.
- [x] Add a compact handoff note (done / remaining / obstacles), author/time,
  conflict protection, accessible from the task without cluttering its main work.
- [x] Test simultaneous requests, user/park isolation, lost responses, conflicts,
  current root-blocker navigation and task drafts.

## Report drafts with photos

- [x] Store photos/blobs in IndexedDB with text and attachment metadata scoped
  by user and park. Preserve existing text drafts and explain storage failures.
- [x] Restore pending photos after reopening; distinguish report creation from
  attachment delivery, retaining incomplete work without duplicating reports.
- [x] Clear only the successfully submitted revision; enforce bounded storage
  and make drafts removable. Test reload, account changes, late replies and quota.

## Diagnostic-rule sample validation

- [x] Evaluate an unsaved rule against bounded stored original samples with
  existing pure matcher; show matches, misses and overlap with current rules.
- [x] Admin/owner only; no raw secrets or background upstream requests. Keep
  single-sample preview and explicit publication separate from this evaluation.
- [x] Test permissions, sample bounds, malformed samples, overlap and no writes.

## Owner host status

- [x] Add an authorized read-only snapshot: disk/RAM, process/runtime health,
  recent integration timings/errors and last successful backup (or unknown).
- [x] Add UI warnings with observed values and timestamps. Shared cached polling
  at >=30 seconds; no public metrics or secret paths/config values.
- [x] Test permission boundaries, missing platform metrics, cache freshness and
  backup failure/success interpretation; no invented host health claims.

## Integration and delivery

- [x] Review changes independently. Backup local SQLite before new migrations.
- [x] Run full API/web tests, lint/build, relevant/full Chromium, check data
  isolation and responsive UI; measure added background requests.
- [x] Apply to current local runtime, verify readiness, document behavior and
  limitations, commit locally. Do not push, merge or publish the Tuna tunnel.

Base: `11270f2`, worktree `operations-interface-redesign`.
Armbian 26 / 8 GB and Tuna remain the production target; target access is absent.

## Verification outcome

Implemented all six approved improvements. API: 1178 passed; web: 1648 passed;
Chromium: 209 passed plus final photo-draft regression rerun. Build, lint,
Ruff, navigation and contrast checks passed (existing warnings documented).
Independent review findings corrected, including late IndexedDB generation
responses and authorization/replay ordering.

200-session/two-worker fixture with presence: 3400 requests across all phases,
no unexpected responses/timeouts; sustained 66.57 RPS, P95 223.12 ms.
This is a Mac/local-stub result, not Armbian/Tuna certification.

Local SQLite backup verified before applying 0022; API restarted, direct and
proxied readiness returned 200, database integrity check passed. Changes
retained in the existing worktree and local branch; no remote publication.
