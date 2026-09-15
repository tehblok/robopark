# Task 9 report — full lifecycle browser and resilience verification

## Delivered

- Extended the isolated JSON-lines browser bridge with guarded (`ROBOPARK_BROWSER_BRIDGE=1` or the dedicated `task-lifecycle` E2E mutation) Tracker availability, deterministic transition fixtures, outbox drain, field values and per-action delivery counts. No control is registered in the production FastAPI app.
- Added desktop full-lifecycle and 390×844 recovery browser scenarios, including duplicate activation, lifecycle timeline, handoff, existing/new completion comments, one code/photo, operator return/approve and exactly-once upstream assertions.
- Replaced retired manual-Tracker card/collaboration assertions with lifecycle ordering, 44px touch targets, no-overflow, disclosure focus, keyboard submission, accessible sync status and duplicate handoff coverage. Retained robot-description filtering coverage.
- Added a 200-authenticated-session local API load test: mixed list/detail/timeline reads plus 50 duplicated claims, worker drain and unique-action upstream bounds.
- Recorded reproducible evidence in `docs/capacity/2026-09-15-task-workflow-200-records.json`; the shared benchmark now emits and uses the repository 1000ms local p95 threshold.

## Verification

- Focused Playwright: 4/4 passed in 7.7s, then corrected desktop case 1/1 passed in 4.5s. A final rerun after restoring the unchanged description case was blocked by escalation auto-review capacity; no test process remained running. The description case had passed earlier.
- Load: 1 passed in 2.26s. Evidence run: 200/200 status 200, p95 304.55ms (<1000ms), 50 unique actions, 50 upstream transitions, backlog 0.
- Scoped Ruff check/format: clean (3 files).
- Web Oxlint: exit 0 with existing warnings. TypeScript, navigation and production build: passed (`check-nav: ok`, 2188 modules built).
- Full API: 1669 passed, 1 skipped, 2 `test_release_signing.py` failures because `pack-release.sh` rejects a dirty worktree; rerun after commit.
- Full web: 1961 passed across 137 files. The four stale Task 8 assertions were updated without reducing coverage: the `СУРП` brand is asserted, dashboard users are allowed onto Work while an unrelated permission remains denied, and the retired admin Tracker URL must preserve `park=7` while redirecting to Work without manual filters.
- Full Ruff format found five pre-existing unformatted files outside Task 9 ownership; scoped files are formatted.

## Completion-gate notes

- Parent verification reports a real copied database upgraded from revision 0022 to 0028 and restored to a byte-identical pre-cutover snapshot.
- Production queue transition mapping and independent authorization/idempotency/attachment/worker review remain parent-level completion-gate work; this task used deterministic test fixtures only and never contacted external services.
