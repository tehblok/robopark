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
