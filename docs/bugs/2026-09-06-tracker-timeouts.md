# Tracker timeouts during task navigation

Reported from the local demo on 2026-09-06: `/work` timed out after 30 seconds.

## Reproduction and cause

1. Open a task with related closed tasks in a park with a large closed history.
2. The related request sent `robot_exact`, but the upstream query omitted the robot.
   All closed tasks in the park were fetched before local identity filtering.
3. SDK `Resource` and `Reference` attribute access silently fetched metadata and
   dereferenced users/components repeatedly. A bounded sample of 50 closed tasks
   took 5.71 seconds; the full park contained 2811 closed tasks. Long requests
   occupied the shared Tracker worker pool and delayed the ordinary open queue.

Normalization now reads loaded SDK values and resolves each user login once per
search. Real logins remain available. The same bounded sample took 1.59 seconds.
Related queries include the supported indexed robot spellings before the existing
exact-identity, park-scope, deduplication and pagination checks. A count-only probe
for the inspected robot narrowed the candidate set from 2811 to 2.

Do not use the entire park history as a diagnostic probe. The full external
diagnostic scan was rejected by automatic review; use bounded samples and scoped
queries instead. Closed-history pagination for a whole park remains a separate
performance concern at larger data volumes.

## Test isolation bug found during verification

`clear_response_caches` could run before `disable_live_merge_by_default`, so the
suite briefly opened and cleared the configured demo's shared cache. A concurrent
API write then failed renaming its deleted temporary file and returned HTTP 500.

The cleanup fixture now explicitly depends on disabling shared caching. The
subprocess regression in `test_test_isolation.py` uses a temporary stand-in for a
demo cache and verifies that its sentinel survives both fixture setup and teardown.
It failed before the fix and passed afterward. Never point test cache cleanup at
a running demo, even when the test database itself is isolated.

## Regression coverage

- Real SDK Resource/Reference objects: 50 rows preserve fields and logins with one
  user lookup instead of 151 metadata/reference lookups.
- SDK versus REST parity for status, resolution, queue, priority and attachments.
- Related-query narrowing retains exact robot and authorization checks.
- Browser test teardown treats cancelled animations as settled, and deliberate
  diagnostic bridge shutdown finishes browser requests without unhandled rejection.
  Unexpected bridge exits and direct test API calls still fail.

Final verification: API 1056 tests, Web 1496 tests, Chromium 193 tests; lint,
build, navigation and contrast checks passed. Live verification loaded the eight
open park tasks, an issue detail, and its two related closed tasks. No task was
modified during the checks.
