# Task 2 report: Overview, Analytics and SLA controls

## Status

Implemented from base `eec9abe`. Overview and Analytics now use one park-scoped Operations model and endpoint, with shared URL state, metrics, task/SLA panels, leadership data, and an accessible dependency-free flow chart. The reusable SLA policy editor is implemented and tested but intentionally not mounted; the Management plan owns that integration.

No production API/backend behavior was changed. The only API-side edit is a compatibility assertion aligned with the backend's existing role defaults.

## TDD evidence

The preserved RED-first work was continued rather than replaced. The initial focused run covered five files and produced 31 failures / 8 passes because the shared Insights components were still stubs. The implementation was then driven through focused component, wrapper, URL/access, ownership, and compatibility tests. The final full web run is 72 files / 1141 tests passing.

## Implementation

- Added typed frontend contracts for the Task 1 Operations overview, flow, SLA and policy DTOs, plus `operationsOverview`, `operationsSlaPolicy`, and `updateOperationsSlaPolicy` API calls.
- Replaced the legacy role-specific Overview and operator-only Analytics branches with `InsightsPage`, so both surfaces use the same request/model functions and chart.
- Made `park`, `days` (`1`, `7`, `30`) and status shareable through the URL. Invalid periods and statuses outside the role's backend bucket scope normalize to the safe default instead of broadening access.
- Added workflow filters for `new`, `moving`, `queued`, `diagnostics`, `waiting_team`, and `waiting_parts`, while retaining supported terminal/raw filters.
- Added selected-park Operations fixtures for role and browser tests. Driver, mechanic, operator, admin and royal fixtures now match `DEFAULT_ROLE_PERMISSIONS` in the backend.
- Updated robot compatibility assertions for the unified workspace: driver defaults now permit scoped related-work reads, refresh uses `Обновить данные робота`, and lazy image expectations account for the identity image plus only the selected diagnostic view.
- Updated the driver registration compatibility assertion to require the existing dashboard/task/robot/check/report read-create defaults while explicitly denying Tracker writes, attachments, and report resolution.

## Security and ownership

- Operations reads require an approved, non-password-reset account with `tracker.read` plus the relevant navigation permission.
- Cache keys are nonpersistent and include principal id/name, Tracker login, role, access status, password-reset state, sorted effective permissions, assigned park identities, selected park identity, period, and status.
- A park/account/role/permission/assignment/queue change synchronously releases the old owner. Resource invalidation advances the pending-load generation, so late success or denial cannot repaint or repopulate a released owner.
- A 401/403 revalidation removes protected data immediately, invalidates only that principal's Operations cache prefix, and refreshes auth once. Same-owner offline/timeout/server revalidation may retain the last in-memory result with an explicit warning.
- The SLA policy editor is visible only with the exact `parks.manage` permission. It never saves speculatively, validates integer values from 1 through 8760, supports explicit clearing with `null`, and guards late save results across park/owner changes.

## Metric definitions and limitations

- Arrival means a task created in Tracker.
- Exit means a task resolved as `fixed`, placed by resolution time.
- Load means current open tasks in the accessible Operations scope.
- SLA age is calendar time since Tracker creation, 24/7 with no pause subtraction; risk begins at 80% of the configured target and overdue is strictly after the target.
- These values do not claim physical robot movement or operator productivity. Leadership panels are rendered only when the endpoint returns their arrays.
- Observed zero is shown as zero. Missing two-hour buckets remain gaps and table cells say `Нет данных`; no-history state is not converted into zero throughput. All labels use `Europe/Moscow` independent of browser timezone.
- A missing SLA target is shown as `Норматив SLA не задан`; it is never described as zero overdue.
- Task and overdue lists disclose truncation reported by the endpoint. Historical coverage and excluded legacy-definition buckets are disclosed next to the chart.

## Compatibility and migrations

No schema or data migration is required by this task. The frontend consumes Task 1's existing Operations contract. Legacy `overviewData`, `overviewModel`, `OverviewSections`, and their stylesheet remain in the tree for safe integration cleanup, but the Overview route no longer imports them.

The unrelated working-tree change in `docs/superpowers/plans/2026-09-03-access-reports-management.md` was preserved and excluded from this task's staging/commit.

## Self-review

- Verified frontend DTO fields and nullable semantics directly against `operations_schemas.py` and endpoint query names against `routers/operations.py`.
- Verified role fixtures against `DEFAULT_ROLE_PERMISSIONS` in `services/rbac.py`.
- Found and fixed a browser-only contrast issue on Operations task links.
- Found and fixed a policy-editor error-state bug where a failed save could be mislabeled as a failed load; added regression coverage.
- Confirmed task links preserve the selected park and royal reads the selected park instead of a fleet-only legacy summary.
- Confirmed responsive task links meet the 44px target in the tested phone layouts and the Operations pages have no serious axe violations in the focused browser suite.
- No unresolved correctness or security concerns were found in the task diff. Existing lint warnings are in pre-existing files outside this task.

## Verification

- Focused API: `.venv/bin/pytest -q tests/test_access_requests.py tests/test_operations.py` — 65 passed; one external Starlette/httpx deprecation warning.
- Full web unit: `vitest run` — 72 files, 1141 tests passed.
- TypeScript: `tsc -b --pretty false` — passed.
- Lint: `oxlint src e2e` — exit 0; six pre-existing warnings outside Task 2 files.
- Browser: Operations overview plus robot compatibility specs on Chromium — 21 passed, including operator/admin phone scenarios and axe checks.

The plan's combined cross-task web/API/build/navigation/contrast acceptance remains a controller-level integration run after all plan tasks finish.
