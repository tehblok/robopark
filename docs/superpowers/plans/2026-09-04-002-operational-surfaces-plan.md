# Operational Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Overview, Analytics, Work, Robots, Administration, and Reports distinct role-aware workflows in one responsive visual system.

**Architecture:** Overview keeps the current operational contract; Analytics gains a historical contract with coverage metadata. Shared list/detail and metric primitives prevent each domain from inventing its own geometry while domain models retain permission logic.

**Tech Stack:** React 19, TypeScript, CSS tokens, FastAPI, SQLAlchemy, Vitest, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-04-operations-interface-redesign-design.md`

## Global Constraints

- Overview is current-shift triage; Analytics is historical investigation.
- Work remains oldest-first and every mutation is capability-gated by the API.
- Robot lists use batch summaries, never one Emergency request per row.
- Existing light/dark tokens and fixed desktop/mobile navigation are mandatory.

---

### Task 1: Shared operational layout primitives

**Files:**
- Create: `apps/web/src/design-system/layout/MasterDetail.tsx`
- Create: `apps/web/src/design-system/layout/MasterDetail.css`
- Create: `apps/web/src/design-system/data/MetricCard.tsx`
- Create: `apps/web/src/design-system/data/EntityRow.tsx`
- Test: `apps/web/src/design-system/layout/MasterDetail.test.tsx`
- Test: `apps/web/src/design-system/data/EntityRow.test.tsx`

**Interfaces:**
- Produces: `MasterDetail({list, detail, detailOpen, onBack})`.
- Produces: `MetricCard({label, value, delta, tone})` and `EntityRow({title, meta, status, actions})`.

- [ ] Write tests for desktop two-column layout semantics, mobile back action, status naming, and 44 px action targets.
- [ ] Run `cd apps/web && npm test -- src/design-system/layout/MasterDetail.test.tsx src/design-system/data/EntityRow.test.tsx` and confirm modules are absent.
- [ ] Implement the three primitives using design tokens only; CSS switches to one visible pane below 900 px.
- [ ] Re-run the focused tests and `npm run check:contrast`.
- [ ] Commit with `git commit -m "feat(web): add operational layout primitives"` after adding the six files.

### Task 2: Role-aware Overview

**Files:**
- Modify: `apps/web/src/domains/shift/OverviewPage.tsx`
- Modify: `apps/web/src/domains/shift/OverviewSections.tsx`
- Modify: `apps/web/src/domains/shift/overviewModel.ts`
- Modify: `apps/web/src/domains/shift/overview.css`
- Test: `apps/web/src/domains/shift/OverviewPage.test.tsx`
- Test: `apps/web/src/domains/shift/overviewModel.test.ts`

**Interfaces:**
- Produces: `buildOverviewModel(data, role)` with alerts, status cards, flow summary, attention queue, workload, and operator-account diagnostics.

- [ ] Add failing tests that driver sees only new/moving, mechanic sees queued/diagnostics, privileged roles see status selection, and overdue tasks precede merely old tasks.
- [ ] Run the two focused tests and confirm current generic composition fails.
- [ ] Build the model and compose alert strip → statuses → flow → attention queue → workload. Preserve data coverage warnings.
- [ ] Run focused tests and `apps/web/e2e/operational/overview.spec.ts`.
- [ ] Commit with `git commit -m "feat(web): turn overview into shift triage"`.

### Task 3: Historical Analytics API and screen

**Files:**
- Create: `apps/api/src/robopark_api/analytics_schemas.py`
- Create: `apps/api/src/robopark_api/services/analytics.py`
- Create: `apps/api/src/robopark_api/services/analytics_history.py`
- Create: `apps/api/src/robopark_api/routers/analytics.py`
- Create: `apps/api/alembic/versions/0018_analytics_observations.py`
- Create: `apps/api/tests/test_analytics.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/web/src/pages/Analytics.tsx`
- Create: `apps/web/src/domains/analytics/analyticsModel.ts`
- Create: `apps/web/src/domains/analytics/AnalyticsWorkspace.tsx`
- Create: `apps/web/src/domains/analytics/analytics.css`
- Test: `apps/web/src/domains/analytics/AnalyticsWorkspace.test.tsx`

**Interfaces:**
- Produces: `GET /analytics?park_id=&days=&bucket=` returning series, backlog age bands, SLA trend, observed stage durations, workload, coverage, and drilldown task keys.
- Produces: Analytics filters independent of Overview URL state.

- [ ] Write pytest cases for bucket totals, missing-bucket coverage, park authorization, and no fabricated zero values.
- [ ] Run `cd apps/api && uv run --frozen --extra dev pytest tests/test_analytics.py -q` and confirm 404/module failure.
- [ ] Persist two-hour task-status observations keyed by park, issue, status, and bucket; derive stage duration only between observed status changes and mark it unavailable until two observations exist. Aggregate existing flow history plus these observations; every metric includes `observed_buckets`, `expected_buckets`, and `complete`.
- [ ] Write the failing React test asserting historical headings and absence of «Текущие задачи».
- [ ] Implement the analytics workspace with period, park comparison, granularity, trends, age bands, SLA, workload, and drilldown links.
- [ ] Run API and web focused tests, then commit with `git commit -m "feat: separate historical analytics from overview"`.

### Task 4: Work queue and related robot tasks

**Files:**
- Modify: `apps/web/src/domains/work/workData.ts`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/work.css`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Modify: `apps/web/e2e/operational/workspace-navigation.spec.ts`

**Interfaces:**
- Produces: immutable `oldestFirst(issues)` and separately loaded related robot task panel.

- [ ] Add failing tests with out-of-order timestamps and related tasks; assert oldest-first and open-before-closed.
- [ ] Run focused tests and confirm order/layout failure.
- [ ] Use `MasterDetail` and `EntityRow`; load related tasks only after the selected issue yields a robot number.
- [ ] Run focused tests and the work browser journey.
- [ ] Commit with `git commit -m "feat(web): streamline the task workbench"`.

### Task 5: Robot registry and unified robot workspace

**Files:**
- Create: `apps/api/src/robopark_api/routers/robot_registry.py`
- Create: `apps/api/src/robopark_api/services/robot_registry.py`
- Create: `apps/api/tests/test_robot_registry.py`
- Modify: `apps/web/src/domains/robots/RobotsPage.tsx`
- Modify: `apps/web/src/domains/robots/RobotPage.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckPage.tsx`
- Modify: `apps/web/src/domains/robots/RobotDetailView.tsx`
- Modify: `apps/web/src/domains/robots/robots.css`
- Test: `apps/web/src/domains/robots/RobotWorkspace.test.tsx`

**Interfaces:**
- Produces: `GET /robots?park_id=&query=&state=` batch rows with identity, cached telemetry summary, error count, and task count.
- Produces: `/robots/:robotId` as canonical card/check route; old check URL redirects while retaining tab query.

- [ ] Add API tests asserting one batched Tracker fetch and zero per-row Emergency fetches.
- [ ] Implement registry aggregation from scoped Tracker issues and existing Emergency cache only.
- [ ] Add failing UI tests for filters, quick actions, unified tabs, and canonical redirect.
- [ ] Implement registry rows and unified workspace using `MasterDetail`; keep detailed Emergency fetch inside the opened robot only.
- [ ] Run robot API/UI/browser tests and commit with `git commit -m "feat: build the robot registry and unified card"`.

### Task 6: Administration and Reports visual migration

**Files:**
- Modify: `apps/web/src/domains/management/ManagementPage.tsx`
- Modify: `apps/web/src/domains/management/UserManagementPage.tsx`
- Modify: `apps/web/src/domains/management/RoleManagementPage.tsx`
- Modify: `apps/web/src/domains/management/management.css`
- Modify: `apps/web/src/pages/Admin.tsx`
- Modify: `apps/web/src/pages/Reports.tsx`
- Modify: `apps/web/src/components/reports/ReportList.tsx`
- Modify: `apps/web/src/components/reports/ReportDetail.tsx`
- Modify: `apps/web/src/index.css`
- Test: `apps/web/src/domains/management/ManagementPage.test.tsx`
- Create: `apps/web/src/domains/management/UserManagementPage.test.tsx`
- Test: `apps/web/src/pages/Reports.test.tsx`

**Interfaces:**
- Consumes: shared operational primitives from Task 1.
- Produces: stable admin subsections and list/detail reports with existing permission contracts unchanged.

- [ ] Add failing tests for active parent navigation, effective permissions preview, owner password/role/park/delete actions, destructive-action separation, and mobile report detail back navigation.
- [ ] Run focused tests and confirm the current long-form layouts fail.
- [ ] Migrate administration and reports to shared panels, rows, status badges, and master-detail; do not widen API permissions.
- [ ] Run management/report tests plus `npm run lint`, `npm run build`, and Playwright responsive journeys.
- [ ] Commit with `git commit -m "feat(web): unify administration and reports surfaces"`.

### Task 7: Full slice verification

- [ ] Run `./scripts/verify.sh api`.
- [ ] Run `./scripts/verify.sh web`.
- [ ] Run `cd apps/web && npm run test:e2e:linux`.
- [ ] Inspect all changed visual baselines in both themes and update only intentional differences.
- [ ] Commit verified Linux baselines with `git commit -m "test(web): refresh operational visual baselines"`.
