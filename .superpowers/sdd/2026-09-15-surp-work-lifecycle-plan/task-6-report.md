# Task 6 report: Five-hour SLA and stable work ordering

## Result

Tracker issue DTOs now expose `queued_at`, `sla_deadline` and `sla_source`. The latest
transition into `queued` from Tracker status history is preferred; a valid issue creation
timestamp is an explicitly marked estimate, and absent or malformed timestamps stay null.
Deadlines are exactly five elapsed hours after the selected timestamp.

The work queue sorts by queued time oldest-first and preserves input order for equal or
unknown timestamps. Tracker search requests an oldest-created upstream order, while the web
boundary re-sorts malformed pages without mutating their arrays. Existing related-repair
pagination retains its prior deterministic ordering contract.

`RepairSla` renders one accessible live status string, including remaining/overdue time and
the estimate disclosure. `WorkPage` owns one timeout aligned to the next minute boundary and
passes the shared `now` value through every work card; cards create no timers. The existing
wrapping grid is reused for phone and desktop layouts without viewport-specific behavior.

## TDD and verification

- RED API: `2 failed, 35 passed`; failures proved missing SLA fields and unstable key-based
  tie handling.
- RED web: missing `RepairSla` plus two expected queue-ordering failures.
- GREEN API: `37 passed, 1 pre-existing Starlette/httpx warning` in
  `tests/test_tracker_read.py`; Tracker normalization compatibility: `8 passed, 1 warning`.
- GREEN web: `3 passed files, 92 passed tests` across `workData`, `RepairSla`, and
  `IssueWorkbench`.
- Static checks: Ruff check clean, Ruff format check clean, oxlint clean, TypeScript build
  clean, and `git diff --check` clean.

## Changed files

- `apps/api/src/robopark_api/services/tracker_client.py`
- `apps/api/src/robopark_api/routers/tracker_read.py`
- `apps/api/src/robopark_api/schemas.py`
- `apps/api/tests/test_tracker_read.py`
- `apps/web/src/api.ts`
- `apps/web/src/domains/work/workData.ts`
- `apps/web/src/domains/work/workData.test.ts`
- `apps/web/src/domains/work/RepairSla.tsx`
- `apps/web/src/domains/work/RepairSla.test.tsx`
- `apps/web/src/domains/work/WorkPage.tsx`
- `apps/web/src/domains/work/IssueWorkbench.tsx`

`WorkPage.tsx` and `IssueWorkbench.tsx` are the minimal composition changes required to make
the one shared minute clock reach all cards; no other work-domain behavior was changed.

## Fix round 1: SDK Resource changelog fallback

Real `yandex-tracker-client` resources keep issue fields in a dictionary `_value`, while the
status history remains available only through the resource's `changelog.get_all()` accessor.
The original mapping guard inspected `_loaded_value(issue)` and therefore returned early for
these resources. The guard now distinguishes an actual dictionary from an SDK Resource.
Embedded history and raw-dictionary fallback behavior are unchanged; incomplete SDK search
projections without a loaded status retain the existing no-hydration path, while a work issue
with loaded status performs exactly one changelog read during normalization.

- RED: the Resource-shaped regression failed with `changelog.calls == 0`.
- GREEN: the regression verifies one changelog call, the exact queued timestamp, the
  `status_history` source, the five-hour deadline, and queue ordering against an estimate.
- Verification: `tests/test_tracker_read.py` and `tests/test_tracker_issue_fields.py` pass;
  focused Ruff check and format check are clean.
