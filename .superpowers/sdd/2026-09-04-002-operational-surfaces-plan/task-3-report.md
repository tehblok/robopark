# Task 3 — Historical Analytics API and screen

Status: COMPLETE. Implemented against `task-3-brief.md` and the dispatch's binding architecture. Base inspected: `abc6fb3`; migration chain had a single head, `0017_driver_work_reports`. No migration conflict and no revision renaming. Commit message: `feat: separate historical analytics from overview`.

## Delivered contract and persistence

- `GET /analytics?park_id=<id>&days=<1..30>&bucket=<2h|1d>` reads persisted history only. Default period is 7 days; default granularity is `1d`. It does not fetch Tracker during a request or call `operations/overview` / `build_overview`.
- Dedicated Pydantic and TypeScript contracts return `series` (`arrived`, `departed`, `backlog`), `backlog_age_bands`, `sla_trend`, `stage_durations`, `workload`, `coverage`, `drilldown_task_keys`, and explicit warnings.
- Each metric and each series point has `period`, `unit`, `value`, `observed_buckets`, `expected_buckets`, `complete`, `sample_count`, and real `task_keys`. Every series declares its aggregation. Coverage always counts base two-hour intervals, even for daily display points.
- Revision/file remains exactly `0018_analytics_observations`, with `down_revision = 0017_driver_work_reports`.
- `analytics_snapshots`: composite primary key `(park_id, bucket_start)`; actual `observed_at` and nullable `target_hours` captured during collection. A successful empty source read creates a snapshot and therefore represents a measured zero backlog.
- `analytics_observations`: composite primary key `(park_id, bucket_start, issue_key, status)`; stores actual raw status, canonical stage bucket, nullable age at observation. Composite FK to the snapshot cascades on deletion; snapshots reference parks with cascade. No employee identity or ranking is persisted.
- The existing two-hour history worker also collects analytics state. A flow failure still permits state collection. Immutable first-success snapshots make repeated runs in the same bucket idempotent. The collector reads the complete paginated open-blocker source directly, verifies queue/tag, deduplicates issue keys, and retains 30 days. Failed reads create no snapshot and no zero. The worker test was redirected to a temporary DB so the new read cannot touch a developer DB.

## Aggregation semantics and limits

The rolling period ends at the current even-hour UTC boundary and excludes the open bucket. It starts exactly `days * 24h` earlier. `1d` is a 24-hour grouping from that period start, not a local calendar date; the UI calls it “24 часа”. Display times are Moscow time.

- Flow sums use only closed, aligned, scanned `definition_version=2` history. Legacy, unscanned, misaligned and current/open intervals are excluded. An observed zero remains zero; a wholly missing interval is `null`. Partially observed daily sums remain partial sums with incomplete coverage.
- Backlog, age groups and stage workload are means over available snapshots, not sums of repeated task sightings. Age groups are `<24h`, `24..72h`, `>=72h`, plus unknown age. When all ages are unknown, known-age groups are unavailable; partial age knowledge keeps `complete=false`. A successfully measured empty park can have zero in all groups.
- SLA is the percentage of overdue task observations among evaluated task observations, weighted by evaluated count. The normative target is the policy captured in each snapshot; later policy edits do not rewrite historical SLA. Unknown policy/age and zero evaluated tasks never produce a fabricated 0%.
- Stage duration is the arithmetic mean of observed residence intervals: first observation of a status through first observation of a different raw status within the requested period. A repeated same-status observation alone provides no duration. A successful snapshot where a task is absent breaks the interval. Duration remains null without an observed transition; transition count and snapshot coverage are reported. No creation timestamp or rounded Tracker age is substituted for stage duration.
- Durations are sampled estimates, not exact workflow event timestamps. Collection covers open blockers: disappearance/closure is not manufactured into a terminal transition. Stages already in progress before the requested period are measured only from their first observation inside that period. Missing collection intervals reduce coverage; transitions between snapshots can be missed.
- Existing flow counters do not retain task keys; their lists are empty and this limitation is visible. All provided drilldown keys come from actual persisted observations, preserve the corresponding park in `/work/<key>?park=<id>`, and use the existing task detail authorization. A task's live status/scope may have changed since observation.
- Authorization requires approved access plus `nav.analytics` and `tracker.read`, active park membership (or admin/royal scope), and the existing role status scope. A user with only `nav.dashboard` cannot read Analytics. Restricted role flow totals are unavailable because historical counters cannot be filtered by status. Comparisons make separate authorized park requests and expose only available parks.
- Analytics owns URL state `period`, `bucket`, `compare`; it ignores/removes the previous Overview `days` / `status` parameters. It has an independent request lifecycle, cancels stale results on ownership/filter change, and fails closed on 401/403 with one auth refresh. It uses no persistent result cache.

## UI

The dedicated workspace presents process trends, age groups, historical SLA, observed stage durations, and average workload by stage. It has no current-shift queue or “Текущие задачи” heading. Comparison adds a metric table with per-cell coverage followed by each park's history. Missing chart points are not connected. Per-interval tables provide accessible values; drilldowns link actual observed tasks. Coverage and unavailable-SLA notices remain visible. Existing design tokens provide light/dark colors, responsive layouts and 44px controls/links.

## RED / GREEN evidence

Initial API command:

```text
cd apps/api
PYTHONPATH=src /Users/tehblokdan/Desktop/Проекты/robopark/apps/api/.venv/bin/python -m pytest tests/test_analytics.py -q
14 failed, 1 warning
```

Expected RED: new endpoint returned 404; aggregation/history modules and observation models did not exist. Cases covered bucket totals, missing coverage, measured vs fabricated zeros, park/permission authorization, stage duration, source failure and historical drilldown keys.

Initial React command:

```text
cd apps/web
/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/vitest/vitest.mjs run src/domains/analytics/AnalyticsWorkspace.test.tsx
4 failed | 2 passed
```

Expected RED: old Analytics rendered the shared operations page, had no historical headings/filters and called the wrong client method. Additional comparison-table expectation was RED before table implementation. The no-known-age regression was also observed RED (`1 failed, 16 passed`) before fixing unavailable age groups.

Final focused results:

```text
PYTHONPATH=src <api-python> -m pytest tests/test_analytics.py tests/test_models_migration.py -q
29 passed, 1 warning
<node> node_modules/vitest/vitest.mjs run src/domains/analytics/AnalyticsWorkspace.test.tsx src/pages/Analytics.test.tsx
2 files passed; 7 tests passed
```

`<api-python>` means `/Users/tehblokdan/Desktop/Проекты/robopark/apps/api/.venv/bin/python`.
`<node>` means `/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`.

## Required verification

All commands executed in the task worktree with existing dependencies; no install or dependency changes.

| Verification | Command (relative to apps/api or apps/web) | Observed result |
| --- | --- | --- |
| Full API | `PYTHONPATH=src <api-python> -m pytest -q` | **719 passed**, 1 existing Starlette/httpx deprecation warning, 87.01 s |
| Full web unit | `<node> node_modules/vitest/vitest.mjs run` | **1334 passed**, **87 files**, 15.04 s |
| TypeScript | `<node> node_modules/typescript/bin/tsc -b` | Exit 0 |
| Production build | `<node> node_modules/vite/bin/vite.js build` | Exit 0; 1976 modules; existing >500 kB bundle advisory |
| Contrast | `<node> scripts/check-contrast.mjs` | Exit 0; all light/dark pairs pass (lowest printed ratio 6.12) |
| Ruff | `<api-python> -m ruff check apps/api/src apps/api/tests apps/api/alembic` from worktree root | `All checks passed!` |
| Whitespace | `git diff --check` | Exit 0 |
| Analytics browser regression | `<node> node_modules/@playwright/test/cli.js test e2e/operational/analytics.spec.ts --workers=2` | **4 passed**, 7.7 s |

Browser matrix: Chromium light/dark × 320/1440 px. Assertions cover independent period/granularity, no current-tasks heading, explicit missing interval values, authorized park comparison, unchanged page width, WCAG AA via axe, 44px drilldown target, actual task detail navigation with park=8, and zero requests to `/api/operations/overview`. The first sandbox attempt could not bind `127.0.0.1:4173` (`EPERM`); the same scoped local test command succeeded under automatic approval escalation. No external service was used by E2E.

Visual screenshots were captured under `apps/web/test-results/operational-analytics-*` (ignored artifacts). Inspected light 1440 and dark 320 screenshots: tokens, wrapping and contained table scrolling are correct.

Migration evidence includes: single expected head; fresh upgrade to head; metadata/schema parity; upgrade from a populated 0017 database; insert into both new tables; downgrade to 0017 preserving old `(arrived=4, departed=9)` flow; subsequent upgrade to head. No real application database was migrated.

## Changed files

- API new: `analytics_schemas.py`, `services/analytics.py`, `services/analytics_history.py`, `routers/analytics.py`, `alembic/versions/0018_analytics_observations.py`, `tests/test_analytics.py`.
- API integration: `models.py`, `main.py`, `services/blocker_history_job.py`.
- API verification support: `tests/test_models_migration.py`, `tests/test_blocker_history.py`.
- Web new: `domains/analytics/analyticsModel.ts`, `AnalyticsWorkspace.tsx`, `analytics.css`, `AnalyticsWorkspace.test.tsx`, `analytics.test-support.ts`; `e2e/operational/analytics.spec.ts`.
- Web integration: `api.ts`, `pages/Analytics.tsx`, `pages/Analytics.test.tsx`.
- This report.

## Self-review / concerns

Reviewed the query boundaries, park/role scoping, immutable writes, complete-snapshot atomicity, scanner connection ownership, schema/migration parity, null semantics, means vs sums, weighted SLA, observed transitions, actual task links and frontend request ownership. Fixed the unknown-age zero case during review, and isolated the existing worker test before the full suite. No outstanding implementation blocker.

Operational constraints: useful stage history accumulates only after deployment and multiple observations; there is no invented backfill. Snapshot storage scales with the park's open tasks and is retained for 30 days. Flow counters and observations are independent sources with separate coverage. Historical terminal closures and exact transition timestamps remain unavailable under the source model, explicitly described above and in UI copy.
