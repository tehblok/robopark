# Task 7 report — complete dual-interface workflow coverage

## Outcome

- Added an executable acceptance manifest for all 30 canonical routes. Every entry names the Classic and reviewed Interface A composition, authorized audiences, nested tab/dialog/form/file states, and loading/empty/error/stale/denied behavior.
- Every role-visible action in the manifest is linked to a concrete API permission pytest node. The audit reads the referenced files and fails when a test function is missing, so a placeholder assertion cannot satisfy coverage.
- The route/role browser matrix now uses the required 320/390/412/899/1440 widths and includes a restricted custom role in both interfaces. Explicit denial coverage proves protected administration does not retain stale content.
- Preserved the Task 6 shared robot-check owner contract in operational tests: the resolve/snapshot requests are read-like context loading, do not count as writes, and do not multiply through 50 Interface A/Classic cycles.
- Fixed a real A-only route identity defect discovered by the browser cycle. A workflow-less Tracker issue now keeps `Задача <KEY>` and its summary in the visible A task header; Classic remains on its existing detail presentation.
- Strengthened the deferred inventory denial regression: a 401/403 purge followed by a late component response cannot restore protected options. Campaign kind remains consistently immutable while editing, and tab keyboard entry coverage remains green.
- Existing Task 1–6 compositions and approved workflows remain shared rather than duplicated: reports/hard delete, inventory/catalog/receipts/counts/labels/export, campaigns, analytics, user activity/location/device/IP, diagnostic mapping/ignore, PWA/camera/file and management permissions continue through their established owners.

## TDD evidence

The initial route audit failed with the incomplete A route set:

```text
operator-parks, reports, reports-new, report-detail, campaigns,
campaign-detail, admin, admin-settings, admin-users, admin-roles,
admin-robot-check
```

After completing the manifest, the API-node resolver first failed on a deliberately invalid test reference. Replacing every placeholder with a collected permission test made all six audit invariants green.

The workflow-less A task regression was then captured structurally: the `[data-task-zone="header"]` existed but contained no route heading. The minimal shared rendering change made the new regression and all 97 existing IssueWorkbench tests green.

Focused final unit run:

```text
route coverage + IssueWorkbench + inventory denial + campaign kind + Tabs
5 files, 134 tests passed
```

## Verification

- Full web unit suite: `npm test -- --run` — **152 files, 2102 tests passed**.
- Relevant API permission/workflow suite across reports, campaigns, inventory, management, diagnostics, analytics and activity: `./.venv/bin/pytest -q ...` — **418 passed**, one pre-existing Starlette/httpx deprecation warning.
- Analytics, work tabs and interface parity Chromium suite: `npx playwright test e2e/operational/analytics.spec.ts e2e/operational/work-tabs.spec.ts e2e/operational/interface-parity.spec.ts --workers=1` — **20 passed**.
- Targeted route fixtures/denial matrix: `npx playwright test e2e/operational/route-role-layout.spec.ts --grep "restricted role cannot|route fixtures prove|inventory is loaded|operator overview" --workers=1` — **5 passed**.
- Additional restricted role cycles at 390/1440 in both modes and royal 412/899 cycles: **14 passed**.
- A broad operational run completed **83 passing tests with no failure** before it was intentionally stopped after Playwright expanded the full matrix to 1583 cases; the two reported interrupted cases were active at cancellation, not assertions.
- `npm run build` — exit 0.
- `npm run check-nav` — **30 route ids**, OK.
- `npm run lint` — exit 0 with the repository's existing 21 warnings.
- `git diff --check` — clean.

The physical OnePlus camera cannot be exercised from this workspace. Existing browser contracts for camera permission/error/native and fallback scanning, track cleanup, PWA install and selected `File` persistence remain covered; the 50-cycle parity test also proves the selected file and comment draft survive both interface modes without writes.

## Files

- `apps/web/src/app/routing/routeCoverageManifest.ts`
- `apps/web/src/app/routing/routeCoverageManifest.test.ts`
- `apps/web/e2e/operational/route-role-layout.spec.ts`
- `apps/web/e2e/operational/interface-parity.spec.ts`
- `apps/web/e2e/operational/work-tabs.spec.ts`
- `apps/web/src/domains/inventory/InventoryPartsView.test.tsx`
- `apps/web/src/domains/work/IssueWorkbench.tsx`
- `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- `apps/web/src/app/interface/interface-a.css`

The pre-existing uncommitted `progress.md` edit was preserved and is not part of this task's commit.

## Review round 1 (in progress)

- Campaign ticket completion now has a synchronous submission lock in the shared owner. Two submits before React commits `busy` produce one request; a 503 becomes a visible alert and releases the same form for retry.
- Regression: `CampaignsPage.test.tsx` — **12 passed**.
- Campaign detail now has a real mode branch over the shared controller: Interface A composes independent context and workflow zones in a two-column task-first layout, while Classic keeps its original linear composition. A structural regression asserts all three canonical A markers. Campaign tests are now **13 passed** and production build passes.
- Reports list/create/detail now expose route-specific A composition markers and separate list/compose/workflow zones while retaining the single existing `ReportsOwner`; Classic emits none of the A zones. Existing Reports suite: **17 passed**; build passes.

### Evidence and role-matrix closure

- `RouteStateEvidence` now covers all **205** manifest nested states exactly once. Every row records authentication mode, actor role, deterministic fixture classification, trigger where applicable, and a concrete assertion; non-executable route-level states carry an explicit N/A reason instead of a generic selector.
- Added a dedicated parallel Playwright collector with one test per executable case. It covers unauthenticated login/register plus overview, operator parks, work/task, robots/check, inventory, reports, campaigns, analytics, management and diagnostic configuration. Shard 1 completed **16 passed** and shard 2 **15 passed**.
- The Vitest audit enforces exact manifest/evidence bijection, unique ids, kind agreement, selector/assertion requirements, N/A policy, complete action×role allow/deny rows, and resolvable pytest function/parameter ids: **10 passed**.
- Corrected the inventory action mismatches: operator has stock-management, document-post and export permissions and now appears in all three visible allow sets. Catalog archive/merge remains manager/custom-permission only.
- Added the API permission matrix `test_inventory_action_role_matrix`: stock settings allow royal/admin/operator/mechanic/restricted and deny driver; catalog archive/merge allow royal/admin/restricted and deny operator/mechanic/driver. All **12 parameters passed**.
- Made the role/route suite independently parallelizable so the bounded full run completes rather than being manually stopped. A first completed run found four restricted robot-detail fixture mismatches; the UI truthfully renders an unconfirmed VIN when the role lacks Emergency permission. The fixture now accepts the confirmed short number or unconfirmed canonical VIN, and the focused four-case regression passed.

### Review verification

- Full web unit suite: **152 files, 2109 tests passed**.
- Relevant API role/workflow suite: **398 passed**.
- Navigation/history/camera/file/QR/photo/interface parity Playwright suites: **48 passed**.
- Complete role/route/viewport/mode Playwright suite: **846 passed, 294 policy-skipped, 0 failed** across all **1140 collected cases**; unlike the earlier interrupted broad run, this run completed.
- Production build and service worker build: exit 0; navigation audit: **30 route ids**, OK; lint: exit 0 with the repository's existing 21 warnings; `git diff --check`: clean.

## Review round 2

- `DomainPresentation` now keeps one stable wrapper/context/workflow subtree across presentation changes. The unit regression verifies the exact input node, unsaved value and open dialog survive Classic↔A; the browser regression repeats this with unsaved role, user and settings drafts.
- Route-state evidence contains no `not-applicable` rows. All **205** manifest states have a unique evidence id; executable fixtures are collected in both presentations, while delegated states resolve to an exact existing owner-test path and title. Attachments assert the real file input/download link, and the work check case activates and asserts the real tab panel.
- Inventory action evidence is a complete four-action×six-role matrix. Operator is read-only even if the client receives stale permissive grants; mechanic retains stock/document/export writes. Alembic `0032_operator_inventory_read_only` revokes the three legacy operator grants on existing installations and reviewed release metadata names the new head.
- Campaign completion uses a synchronous submission lock. A double submit during a 503 sends one PATCH, exposes the classified alert and unlocks the same form for retry.
- Reports list/create/detail and campaign list now use real A context/workflow/action compositions over the shared owners. Their mobile grids collapse at 899px and below; the focused 52-case overflow regression and the full viewport matrix pass.

### Round-2 verification

- Full web unit suite: `npm test -- --run` — **153 files, 2116 tests passed**.
- Evidence collector: `npx playwright test e2e/operational/route-state-evidence.spec.ts` — **63 passed** (every executable case in Classic and A, plus collector audit).
- Complete route/role/viewport/mode suite from zero: **846 passed, 294 policy-skipped, 0 failed** across **1140 collected cases**.
- Camera/file/history/navigation/diagnostic/overflow browser suite: **84 passed**.
- Focused migration and full-suite failure regressions: **6 passed**.
- Full API suite from zero after the role/migration corrections: **1838 passed, 7 skipped**.
- Production build and service worker build: exit 0; navigation audit: **30 route ids**, OK; lint: exit 0 with the repository's existing 21 warnings.

## Review round 3

- Shortened the Alembic head to `0032_operator_inv_readonly` (22 characters), updated release/acceptance metadata, and added a migration-id length regression. The canonical PostgreSQL 17 gate now performs a real `0031_campaign_snapshot_state` → head upgrade and proves the operator's legacy stock/document/export grants are removed in PostgreSQL, not only SQLite.
- Removed the route-wide evidence-owner fallback. Each delegated state now has its own exact state key, collected title, state kind and trigger contract; the parameterized owner suite executes one assertion per delegated state and rejects route/state/kind/title mismatches. The only N/A is the intentionally API-only report hard purge, with a route-specific impossibility reason and exact HTTP owner.
- Added concrete Classic+A campaign loading, empty, retryable-error and create-form regressions. The browser collector still executes every browser fixture in both presentations and real attachment/check-tab triggers remain executable.
- Replaced generic action evidence with a unique action×role×outcome pytest node. The HTTP matrix covers public auth, park request, task claim/attachment, reports, campaigns, platform settings/ops, users, roles and diagnostics; the inventory HTTP matrix now additionally exercises receipt posting and real CSV export. A RED matrix case exposed the real claim policy (mechanic-only), and the route manifest was corrected to match it.

### Round-3 verification

- Canonical PostgreSQL 17 gate: `./scripts/verify.sh api-postgres` — **7 passed**.
- Exact HTTP action matrices: **140 passed**; pytest collection resolved all **140** parameter ids.
- Full API gate including Ruff/format: `./scripts/verify.sh api` — **1967 passed, 8 skipped**, 21 warnings.
- Full web unit suite: `npm test -- --run` — **153 files, 2301 tests passed**.
- Evidence collector: **63 passed** in Classic and A.
- Complete route/role/viewport/mode suite from zero: **846 passed, 294 policy-skipped, 0 failed** across **1140 collected cases**.
- Production and service-worker build: `npm run build` — exit 0.

## Review round 4

- Replaced the delegated owner-contract string comparison with a collected 347-node Classic/A Playwright suite. Every delegated state mounts its real route owner; tabs, forms, file boundaries and dialogs operate real DOM controls, while loading, empty, error, stale and denied cases control and inspect real fixture API requests. The manifest audit resolves the exact dynamic node ids and requires the mounted-owner, domain-request and transitioned-DOM assertions to remain in the implementation.
- Made report hard-delete executable evidence instead of N/A. Admin and royal fixtures cover Classic and A at mobile and desktop widths, verify the destructive button and alert dialog, require the exact `УДАЛИТЬ` phrase, prove exactly one `DELETE /api/reports/9`, and check both success removal/navigation and 403/503 preservation with an alert. A mutation 403 now remains local to its owning form/dialog; 401 and read-side 403 still invalidate authorization-owned resources.
- Corrected the HTTP action matrix to use real mutation targets for report return, report purge, user management and approval; added exact request-method/path/body contracts and explicit allow status sets. Denials accept only 401/403 and no allow assertion can pass on an arbitrary 5xx response. No production records are used: restore, reports, users and parks are isolated database/file fixtures.

### Round-4 verification

- Exact route/inventory HTTP action matrices: **141 passed**.
- Delegated domain-owner contract collector: **347 passed**.
- Executable route-state evidence: **65 passed**.
- Hard-delete browser matrix: **13 passed**.
- Route coverage manifest/API transport units: **32 passed**.
- Complete route/role/viewport/mode matrix: **846 passed, 294 policy-skipped, 0 failed** across **1140 collected cases**.
- Full web unit suite: **153 files, 2129 tests passed**.
- Full API gate including Ruff/format: **1968 passed, 8 skipped**, 21 warnings.
- Production and service-worker build: `npm run build` — exit 0.

## Review round 5

- Replaced the remaining inferred owner behavior with explicit state drivers. Each of the 161 executable delegated states now names its exact route control or protected selector, and async states name the exact method/path, fixture transition and protected-data assertion. Loading, empty, error, stale and late-denial paths exercise the real owner API; tabs, forms, files and dialogs operate named DOM controls. The collector rejects generic `main`, missing form/file/dialog contracts, mismatched state ids and missing implementation assertions.
- Corrected the evidence manifest instead of inventing production refresh/API behavior. Twelve states are explicit semantic N/A contracts: the synchronous administration landing owns no async request, Analytics has no revalidation owner for stale UI, draft-protecting settings/diagnostic editors disable refresh, campaign list/detail expose no loaded-data revalidation, and the wildcard CatchAll redirects rather than mounting a not-found view. Analytics loading/empty/error/denied continue to use its real `GET /api/analytics` owner.
- Made authentication-failure classification semantic rather than method-only. The central request transport accepts `authFailureScope: 'query' | 'mutation'`, defaults GET/HEAD to query and writes to mutation, and marks both emergency-resolve POST endpoints as query operations. A query 403 now clears validators, protected resource/device caches and protected browser storage while retaining the authenticated session; a report-delete mutation 403 stays local and emits no global authorization failure. Regression coverage also proves a response from the previous authorization generation cannot restore its validator.

### Round-5 verification

- Explicit delegated owner matrix: **323 passed** (161 states in Classic and A plus the exact collector audit).
- Executable route-state evidence: **65 passed**.
- Complete route/role/viewport/mode matrix: **846 passed, 294 policy-skipped, 0 failed** across **1140 collected cases**.
- Focused route manifest/API/auth units: **53 passed**.
- Full web unit suite: `npm test -- --run` — **153 files, 2131 tests passed**.
- Full API gate including Ruff/format: `./scripts/verify.sh api` — **1968 passed, 8 skipped**, 21 warnings.
- Production and service-worker build: `npm run build` — exit 0.
- `git diff --check` — clean.
