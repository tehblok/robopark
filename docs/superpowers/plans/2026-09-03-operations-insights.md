# Operations Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a role-aware Overview and a useful Analytics section with honest flow, workload and configurable SLA calculations.

**Architecture:** A single permission-scoped operations read model supplies current status counts, role-filtered tasks, flow coverage, SLA and leadership workload. Pure functions compute classifications and durations. Overview and Analytics share typed fetching and chart components, not independent definitions.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, React, TypeScript, SVG, Vitest, pytest.

**Spec:** docs/superpowers/specs/2026-09-03-operations-corrections-design.md (Обзор и аналитика).

## Global Constraints

- Worktree `/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/robopark-redesign`; no main merge/push, no live API/database or `.env`/secrets, no dependency installation.
- Node `/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`; API `.venv/bin/python` and `.venv/bin/ruff`.
- API tests must disable Settings env_file before importing pytest: `.venv/bin/python -c 'from robopark_api.config import Settings; Settings.model_config["env_file"] = None; import pytest; raise SystemExit(pytest.main(["-q", "tests/test_operations.py"]))'`.
- All new reads require approved account, applicable section permission and allowed active park. Tracker tasks additionally require tracker.read. No client query expands role scope.
- Driver task visibility: new/moving only, read-only by default. Mechanic Overview defaults: queued/diagnostics. Operator/admin/royal: selectable statuses. Preserve other-role task permissions and explicit per-user denies.
- Metric unknown/missing values are not zero. No fabricated historical performance or operator productivity scores.
- SLA policy: explicit per-park nullable `target_hours`, integer 1..8760; elapsed UTC calendar hours since Tracker creation, 24/7, no paused time subtraction. No default target. Warn at 80% of target, overdue strictly after target. Explain this basis in UI.
- Flow v2: created tickets are entries; fixed tickets with resolution date in window are exits. Legacy Updated-based aggregates must not be presented as v2 exits.

### Task 1: Scoped operations API and metric definitions

**Files:**
- Create: `apps/api/src/robopark_api/routers/operations.py`, `services/operations.py`, `operations_schemas.py`, `tests/test_operations.py`.
- Modify: `main.py` router registration; `services/tracker_filters.py`, `services/tracker_policy.py`, `services/tracker_metrics.py`, `services/blocker_history.py`, `routers/tracker_read.py`, `models.py`, relevant tests.
- Create: `apps/api/alembic/versions/0016_history_definition.py` for `ParkBlockerHistory.definition_version` (existing rows 1, new scanner rows 2).
- Driver default permission migration is owned by the later accounts plan; do not change rbac seed in this task.

**Interfaces:**
- GET `/operations/overview?park_id=<id>&days=7&status=all` → typed `OperationsOverviewOut`: `park_id`, `generated_at`, `timezone`, `status_options` (key/label), `selected_status`, `counts`, `tasks: list[BlockerOut]`, `tasks_total`, `tasks_truncated`, `flow`, `sla`, `workload`, `operators`.
- `flow`: `definition_version=2`, `window_start`, `window_end`, `expected_buckets`, `observed_buckets`, `complete`, `legacy_buckets`, `points[{bucket_start, arrived_count, departed_count}]`. Only closed observed v2 buckets; never manufacture missing zeros. Windows in UTC, UI labels Europe/Moscow.
- `sla`: `target_hours: int|null`, `evaluated_count`, `unknown_count`, `at_risk_count`, `overdue_count`, `overdue` (task identity/status/age/overdue hours). Sort most overdue first; bounded list and exact total.
- `workload`: `{login, display, open_count, overdue_count, oldest_hours}` per current assignee, leadership only. Unassigned gets explicit label, no fake login.
- `operators`: current active approved operator accounts linked to this park (or relevant fleet scope) matched to exact `tracker_login`; show current open load, overdue and oldest age. Missing login yields null stats, not zero. Admin/royal only; no account data for other roles.
- GET/PUT `/operations/sla-policy?park_id=<id>` → `{park_id,target_hours}`; PUT requires parks.manage and authorized park, validates nullable integer range and audits edit. Storage uses non-secret PlatformSetting `operations.sla.park.<id>`, no runtime env.
- New classification aliases must be exact normalized key/display matches: `new`, `open`, `новый`, `новая`, `открыт`, `открыта` → new; `diagnostics`, `diagnostic`, `diagnosis`, `диагностика`, `на диагностике` → diagnostics; existing moving/queued/waiting mappings preserved. Unknown status stays other. Do not map generic inProgress to diagnostics.

- [ ] Add pure and route regression tests before implementation. Cover exact SLA boundary, future/invalid creation time, null policy, 80% warning, unknown statuses, new vs renewed, driver foreign status/detail denial, mechanic selected_status all applying role default, leadership filters, inaccessible park, unapproved account, no tracker.read, upstream error, partial/zero history, legacy history isolation, no operator account data leakage.

```python
def test_status_new_is_not_a_substring_match():
    assert status_bucket("new", "Новый") == "new"
    assert status_bucket("renewed", "Возобновлён") is None

def test_departures_use_resolution_date():
    query = build_done_today_query("ROBOPARK", "north")
    assert "Resolved: today()" in query
    assert "Updated:" not in query
```

- [ ] Run these focused tests RED, then implement typed API, pure calculations, read scopes and error handling. Reuse cached park blockers once per request for counts/SLA/workload. Counts are calculated before list truncation; cap rendered tasks at 200 with truthful tasks_total/truncated. If source has a hard search limit, expose incompleteness rather than claiming fleet totals.
- [ ] Require driver new/moving check in shared tracker_policy before list/detail/comments/actions exposure, preserving assigned queues/tags. Do not grant writes. Do not unnecessarily restrict other roles' Work detail access.
- [ ] Let Work's `/tracker/issues` status filter accept canonical new/moving/queued/diagnostics/waiting_team/waiting_parts buckets by compiling their aliases into a safe OR query. Preserve explicit raw Tracker status filtering. Do not send canonical bucket names as raw custom status keys when they differ. Cover requested bucket compilation and driver scope enforcement with route tests.
- [ ] Correct departure query to `Resolved` per official Tracker query docs: https://yandex.ru/support/tracker/ru/user/query-filter ; response field `resolvedAt`: https://yandex.ru/support/tracker/en/api-ref/issues/response-fields . Keep existing supported date formatting unless verified improvement. Add versioned history migration/scanner handling; never relabel legacy data v2 without recomputation.
- [ ] GREEN focused API suites, Ruff, migration upgrade on isolated temporary SQLite only (no project env). Commit owned files and report exact output and assumptions.

### Task 2: Overview, Analytics and SLA controls

**Files:**
- Modify: `apps/web/src/api.ts`, `domains/shift/OverviewPage.tsx`, `overviewData.ts`, `overviewModel.ts`, `OverviewSections.tsx`, `overview.css`, tests.
- Create: `apps/web/src/domains/insights/operations.ts`, `FlowChart.tsx`, `OperationsPanels.tsx`, `InsightsPage.tsx`, `insights.css`, adjacent tests.
- Modify: `apps/web/src/pages/Analytics.tsx`, `Analytics.test.tsx`, relevant fixture/test routes in `apps/web/e2e/support`.
- Modify: `apps/web/src/domains/work/WorkFilters.tsx` and tests to offer fleet workflow statuses (new, moving, queued, diagnostics, waiting_team, waiting_parts) alongside supported terminal/raw status filters, using Task 1 backend canonical buckets.
- May add SLA policy editor as reusable `domains/insights/SlaPolicyEditor.tsx`; later Management plan mounts it.

**Interfaces:**
- Add `api.operationsOverview(parkId: number, days?: number, status?: string): Promise<OperationsOverview>` and `api.operationsSlaPolicy(parkId)`, `api.updateOperationsSlaPolicy(parkId, {target_hours})` matching Task 1 DTOs exactly.
- Overview and Analytics consume the same request/model functions and chart. Analytics uses days 1/7/30 and park selector already owned by shell, current load/status/SLA panels, operator statistics when returned. No duplicate operator-only legacy page branch.

- [ ] Tests first: driver shows new/moving task queue; mechanic queued/diagnostics; operator/admin/royal status selection sends selected status and park; royal selected park has tasks, not only fleet summary. Reject stale A/B/account responses synchronously; forbidden revalidation clears protected data and refreshes auth once. Do not retain legacy driver static card or summary-only Analytics branch.
- [ ] Chart tests distinguish observed zeros, gaps and no history; numbers in accessible table match SVG data. All chart dates label Europe/Moscow independent of browser timezone. Overview shows arrivals/exits graph and status counts, tasks, SLA. Leadership panels absent for mechanic/driver.

```tsx
expect(screen.getByRole('heading', { name: 'Просрочки SLA' })).toBeVisible()
expect(screen.getByText('Норматив SLA не задан')).toBeVisible()
expect(screen.queryByText('Просрочек нет')).not.toBeInTheDocument()
```

- [ ] Run RED focused tests. Implement shared insights components with existing design-system tokens and responsive layout, no chart dependency. Use scoped nonpersistent cache keys including principal, role, effective permissions and park assignment identity. Old in-flight owner may not mutate new owner or cache after release. Keep URL status/period shareable and normalize invalid input.
- [ ] Provide compact metric definitions: arrival=created task, exit=fixed by resolution date, load=current open tasks. No claims that these are physical robot moves or operator productivity. Policy editor supports clearing target, bounded validation, pending/error/success, exact permission visibility; no speculative target saved automatically.
- [ ] Update focused fixtures to typed API response. GREEN unit suites and TypeScript. Commit and report.

## Integration acceptance

- [ ] Review task diffs, then one combined web/API test/build/navigation/contrast run after other plans finish. Include role/access failures and real browser mobile/desktop checks, not production network.
- [ ] Record calculation definitions, coverage limitations, migrations and test evidence in durable report.
