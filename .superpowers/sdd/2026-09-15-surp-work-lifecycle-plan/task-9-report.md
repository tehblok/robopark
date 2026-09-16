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
- Full API: 1669 passed, 1 skipped, 2 initial `test_release_signing.py` failures. A clean-tree rerun traced both to `deploy/release-metadata.json` still declaring migration 0027 after the 0028 migration landed; the metadata and its exact wrapper expectation were synchronized in the bounded Task 9 fix round. Both pack-release regressions then passed on the clean committed state (2 passed in 2.21s).
- Full web: 1961 passed across 137 files. The four stale Task 8 assertions were updated without reducing coverage: the `СУРП` brand is asserted, dashboard users are allowed onto Work while an unrelated permission remains denied, and the retired admin Tracker URL must preserve `park=7` while redirecting to Work without manual filters.
- Full Ruff format found five pre-existing unformatted files outside Task 9 ownership; scoped files are formatted.

## Completion-gate notes

- Parent verification reports a real copied database upgraded from revision 0022 to 0028 and restored to a byte-identical pre-cutover snapshot.
- Production queue transition mapping and independent authorization/idempotency/attachment/worker review remain parent-level completion-gate work; this task used deterministic test fixtures only and never contacted external services.

## Fix round 2 — UI-only lifecycle proof

- Removed the bridge `submit_review` mutation control. The bridge now exposes only guarded setup/upstream availability, outbox drain and read-only counters/snapshot evidence for lifecycle tests.
- Proved RED with the old bypass scenario (`submit_review` control returned 400), then rewrote the desktop and 390×844 lifecycle around visible controls only: claim, comments, two handoffs, real logout/login role changes, defect-code selection, actual file/camera inputs, operator return, mechanic clarification/resubmit and operator approval.
- Both unique submissions assert exactly two uploads and exactly two `theDefectCode` writes; all delivered action markers are present and each has count one. The rendered DOM timeline is asserted as an ordered sequence, and the comment counter assertion is non-vacuous.
- The retained card test reproduced a real lifecycle-summary regression that exposed raw SUF/Port/Rover/Mode template fields. `TaskIssueSummary` now reuses the existing display-only `summarizeIssueDescription` projection; Classificator, Comment, Zone and repair notes remain visible.
- Final focused Playwright: 5 passed in 13.4s. TypeScript, scoped Oxlint, bridge Ruff check/format and `git diff --check`: passed.

## Fix round 3 — exact upstream comment set

- Replaced the non-empty comment-counter loop with an exact scripted cardinality: seven `comment` actions and two `attach` actions must exist.
- Expected upstream marker keys are derived from those nine persisted stable action IDs. The complete delivered marker-key map must equal the derived set with every value exactly one, so an extra, missing or duplicate user, handoff, return, clarification or attachment comment fails the test.
- Proved RED by deliberately omitting one expected action; Playwright reported the ninth delivered marker as an unexpected key. Final lifecycle Playwright passed desktop and 390×844 (2 passed in 12.3s); the full focused Task 9 set passed (5 passed in 13.0s). TypeScript, scoped Oxlint and `git diff --check` passed.
