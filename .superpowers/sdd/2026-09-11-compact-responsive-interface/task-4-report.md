# Task 4 report

STATUS: COMPLETE

## Scope

- Inventory create, edit, stock movement and printing workflows now use responsive disclosures; catalog/filter summaries remain visible on phones.
- Inventory thumbnails have square `72×72` intrinsic bounds and dense cards.
- Report list rows include park context; selected report history and attachments use a phone disclosure while `MasterDetail` continues to own exclusive list/detail navigation below 900 px.
- Campaign creation, metrics and closed-ticket history use responsive disclosures; active ticket search and summaries remain visible.
- API routes, payload shapes, report photo drafts and `MasterDetail` return navigation were not changed.

## TDD

RED 1:

`cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx src/pages/Reports.test.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Result: exit 1; 3 failed / 27 passed. Expected failures: inventory stock movement was mounted, report attachment was mounted, and campaign metrics were mounted at 390 px.

RED 2:

`cd apps/web && npm test -- --run src/pages/Reports.test.tsx`

Result: exit 1; 1 failed / 16 passed. Expected failure: the compact report row did not include park `Север`.

GREEN:

`cd apps/web && npm test -- --run src/domains/inventory src/pages/Reports.test.tsx src/components/reports src/domains/campaigns`

Result: exit 0; 5 files passed, 49 tests passed.

## Verification

`cd apps/web && npx playwright test e2e/operational/admin-reports.spec.ts e2e/operational/report-photo-drafts.spec.ts`

Result: exit 0; 26 passed (41.6s). Draft photo survived reload and resumed upload.

`cd apps/web && npm run build`

Result: exit 0; 2181 modules transformed, production bundle built.

`cd apps/web && npm run lint -- src/domains/inventory/InventoryPage.tsx src/domains/inventory/InventoryPage.test.tsx src/pages/Reports.tsx src/pages/Reports.test.tsx src/components/reports/ReportDetail.tsx src/components/reports/ReportList.tsx src/domains/campaigns/CampaignsPage.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Result: exit 0; oxlint reported no findings.

`git diff --check -- <Task 4 paths>`

Result: exit 0; no whitespace errors.

## Commit

`git commit -m "feat: compact data-heavy workflows"`

## Concerns

- Vite retains its existing warning that some chunks exceed 500 kB after minification; the build succeeds.
- One final Playwright attempt could not bind the local server inside the sandbox (`EPERM 127.0.0.1:4173`); the required escalated retry passed 26/26.
- No package/version/release files, responsive PNG snapshots or unrelated E2E files are included.

## Fix round 1/5

STATUS: COMPLETE

### Review findings

- Restored a selected-part workflow gate inside inventory disclosures. Desktop cards mount neither edit nor stock forms until a specific part workflow is selected; selecting edit mounts exactly one primary action.
- Campaign list cards retain the campaign-type badge and add visible status badges: `Активна`, `Завершена`, `Просрочена`.

### RED

`cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Result: exit 1; 2 failed / 13 passed. Inventory had 4 primary actions before selection; campaign cards had no text status labels.

### GREEN and verification

`cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Result: exit 0; 2 files passed, 15 tests passed.

`cd apps/web && npm test -- --run src/domains/inventory src/pages/Reports.test.tsx src/components/reports src/domains/campaigns`

Result: exit 0; 5 files passed, 51 tests passed.

`cd apps/web && npm run build`

Result: exit 0; 2181 modules transformed and production bundle built. Existing chunk-size warning remains.

`cd apps/web && npm run lint -- src/domains/inventory/InventoryPage.tsx src/domains/inventory/InventoryPage.test.tsx src/domains/campaigns/CampaignsPage.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Result: exit 0; oxlint reported no findings.

Report Playwright was not rerun because this round did not change report behavior.

## Fix round 2/5

STATUS: COMPLETE

### Review finding

- Added a backward-compatible `onOpenChange` callback to `ResponsiveDisclosure`. The group notifies both the disclosure being closed and the one being opened.
- Inventory uses that callback to select edit or stock movement in the same phone tap. Switching disclosures unmounts the previous form; desktop still requires the compact in-content selector and mounts at most one primary workflow.

### RED

`cd apps/web && npm test -- --run src/design-system/layout/ResponsiveDisclosure.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Result: exit 1; 3 failed / 15 passed. The callback was never invoked, and both phone disclosures mounted only their intermediate selector rather than the requested form.

### GREEN and verification

`cd apps/web && npm test -- --run src/design-system/layout/ResponsiveDisclosure.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Result: exit 0; 2 files passed, 18 tests passed.

`cd apps/web && npm test -- --run src/domains/inventory src/pages/Reports.test.tsx src/components/reports src/domains/campaigns src/design-system/layout/ResponsiveDisclosure.test.tsx`

Result: exit 0; 6 files passed, 58 tests passed.

`cd apps/web && npm run build`

Result: exit 0; 2181 modules transformed and production bundle built. Existing chunk-size warning remains.

`cd apps/web && npm run lint -- src/design-system/layout/ResponsiveDisclosure.tsx src/design-system/layout/ResponsiveDisclosure.test.tsx src/domains/inventory/InventoryPage.tsx src/domains/inventory/InventoryPage.test.tsx`

Result: exit 0; oxlint reported no findings.

Report Playwright was not rerun because this round did not change report behavior.

## Fix round 3/5

STATUS: COMPLETE

### Review finding

- Inventory disclosure close callbacks now clear the shared workflow only when the active workflow belongs to the same part and action. Closing an older card cannot unmount another card's editor or discard its in-progress quantity.

### RED

`cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx`

Result: exit 1; 1 file failed, 1 failed / 13 passed. After opening edit on part A, entering quantity `7` in movement on part B, and closing A, part B's operation and quantity fields were unmounted.

### GREEN and verification

`cd apps/web && npm test -- --run src/design-system/layout/ResponsiveDisclosure.test.tsx src/domains/inventory/InventoryPage.test.tsx`

Result: exit 0; 2 files passed, 19 tests passed.

`cd apps/web && npm test -- --run src/domains/inventory src/pages/Reports.test.tsx src/components/reports src/domains/campaigns src/design-system/layout/ResponsiveDisclosure.test.tsx`

Result: exit 0; 6 files passed, 59 tests passed.

`cd apps/web && npm run build`

Result: exit 0; 2181 modules transformed and production bundle built. Existing chunk-size warning remains.

`cd apps/web && npm run lint -- src/design-system/layout/ResponsiveDisclosure.tsx src/design-system/layout/ResponsiveDisclosure.test.tsx src/domains/inventory/InventoryPage.tsx src/domains/inventory/InventoryPage.test.tsx`

Result: exit 0; oxlint reported no findings.

Report Playwright was not rerun because this round did not change report behavior.

### Commit

`git commit -m "fix: preserve selected inventory workflow"`

### Concerns

- Vite retains its existing warning that some chunks exceed 500 kB after minification; the build succeeds.
- Unrelated dirty package/version/API/release files and responsive visual snapshots remain untouched and unstaged.
