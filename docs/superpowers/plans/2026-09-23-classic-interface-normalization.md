# Classic interface normalization implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Interface A and make one polished Classic interface geometrically consistent on every route, theme and supported viewport.

**Architecture:** First collapse presentation mode to one Classic shell and migrate stored preferences. Then normalize semantic layout/action primitives and rebuild work/inventory/diagnostic surfaces on those primitives. A route matrix test catches overflow, fused panels and inconsistent controls without relying on page-specific overrides.

**Tech Stack:** React 19, TypeScript, CSS custom properties, Vitest/Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-23-classic-workflows-inventory-schedule-design.md`

## Global Constraints

- Only Classic remains; light/dark/system theme and density remain.
- Phone forms are one column and no route has page-level horizontal overflow at 360 px.
- Page/card/control spacing and radii use shared semantic tokens.
- Buttons in one group share height, baseline, padding and state styling.
- Run targeted component and route-matrix tests only; no full E2E suite or soak.

## Review Focus

- Existing local storage contains `task-first`: first paint is Classic with no flash or remount (Task 1 test).
- A 70-character issue key or JSON path at 360 px wraps inside its panel without page overflow (Task 4/5 tests).
- Light and dark themes preserve visible boundaries between nested panels and buttons (Task 2 test).
- Defect/photo action forms on phone remain one column with every control at least 44 px high (Task 4 test).
- Empty/loading/error states use the same outer geometry as successful content (Task 6 route-matrix test).

---

### Task 1: Remove Interface A from runtime and settings

**Files:**
- Modify: `apps/web/src/app/interface/interfaceModeStore.ts`
- Modify: `apps/web/src/app/interface/interfaceModeStore.test.ts`
- Modify: `apps/web/src/app/interface/InterfaceModeProvider.tsx`
- Modify: `apps/web/src/app/interface/InterfaceModeProvider.test.tsx`
- Modify: `apps/web/src/app/interface/PresentationShell.tsx`
- Modify: `apps/web/src/app/interface/PresentationShell.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Modify: `apps/web/src/components/auth/AuthLayout.tsx`
- Modify: `apps/web/src/app/routing/routeCoverageManifest.ts`
- Delete: `apps/web/src/app/interface/InterfaceChoice.tsx`
- Delete: `apps/web/src/app/interface/TaskFirstShell.tsx`
- Delete: `apps/web/src/app/interface/TaskFirstShell.css`
- Delete: `apps/web/src/app/interface/TaskFirstLayout.tsx`
- Delete: `apps/web/src/app/interface/interface-a.css`
- Delete: `apps/web/src/domains/work/TaskFirstTaskLayout.tsx`
- Delete: `apps/web/src/domains/work/TaskFirstWorkbench.tsx`
- Delete: `apps/web/src/domains/robots/TaskFirstRobotLayout.tsx`
- Delete: `apps/web/src/domains/work/TaskFirstTaskLayout.test.tsx`
- Delete: `apps/web/src/domains/work/TaskFirstWorkbench.test.tsx`
- Delete: `apps/web/src/domains/robots/TaskFirstRobotLayout.test.tsx`
- Modify: `apps/web/src/app/interface/DomainPresentation.tsx`
- Modify: `apps/web/src/app/interface/DomainPresentation.test.tsx`

**Interfaces:**
- `InterfaceMode` becomes `'classic'`.
- Stored `robopark:interface:v1:* = task-first` is removed and resolves to Classic synchronously.
- `PresentationShell` always renders `ClassicShell`.

- [ ] **Step 1: Write failing migration/first-paint tests**

```tsx
it('migrates task-first storage without rendering the removed shell', () => {
  localStorage.setItem('robopark:interface:v1:810', 'task-first')
  render(<InterfaceModeProvider accountId={810}><ShellProbe /></InterfaceModeProvider>)
  expect(document.documentElement.dataset.interface).toBe('classic')
  expect(localStorage.getItem('robopark:interface:v1:810')).toBeNull()
})
```

- [ ] **Step 2: Run failing interface tests**

Run: `cd apps/web && npm test -- --run src/app/interface/interfaceModeStore.test.ts src/app/interface/InterfaceModeProvider.test.tsx src/app/interface/PresentationShell.test.tsx src/app/shell/AppShell.test.tsx`

- [ ] **Step 3: Collapse to Classic and delete A assets**

Remove the interface picker from login/profile, dynamic CSS loading, `task-first` branches/data attributes and A-only tests. Keep domain content; only delete wrappers and style branches that have no Classic behaviour.

- [ ] **Step 4: Run tests and static search**

Run: `cd apps/web && npm test -- --run src/app/interface/interfaceModeStore.test.ts src/app/interface/InterfaceModeProvider.test.tsx src/app/interface/PresentationShell.test.tsx src/app/shell/AppShell.test.tsx`

Run: `cd apps/web && ! rg -n "task-first|Новый А|TaskFirst" src --glob '!*.snap'`

- [ ] **Step 5: Commit**

```bash
git add -A apps/web/src/app/interface apps/web/src/app/shell/AppShell.tsx apps/web/src/components/auth/AuthLayout.tsx apps/web/src/app/routing/routeCoverageManifest.ts apps/web/src/domains/work apps/web/src/domains/robots
git commit -m "refactor(ui): remove interface A"
```

### Task 2: Normalize Classic tokens and layout primitives

**Files:**
- Modify: `apps/web/src/app/interface/interfaceTokens.css`
- Modify: `apps/web/src/design-system/styles/tokens.css`
- Modify: `apps/web/src/design-system/layout/PageLayout.css`
- Modify: `apps/web/src/design-system/layout/PageLayout.test.tsx`
- Modify: `apps/web/src/design-system/actions/Button.css`
- Modify: `apps/web/src/design-system/actions/Button.test.tsx`
- Modify: `apps/web/src/design-system/navigation/Tabs.css`
- Modify: `apps/web/src/design-system/navigation/Tabs.test.tsx`
- Modify: `apps/web/src/app/interface/ClassicShell.css`

**Interfaces:**
- Tokens match the spec: 24/16/12 page gutters, 24/16 section gaps, 20/16 card padding, 44/48 control heights, 16 card and 12 control radii.
- `Tabs` supports `variant='primary'|'secondary'` and wraps/scrolls inside its own box without widening the page.

- [ ] **Step 1: Add failing CSS-contract tests**

```tsx
it('keeps grouped actions equal-height and mobile panels separated', () => {
  const css = readFileSync('src/design-system/layout/PageLayout.css', 'utf8')
  expect(css).toMatch(/\.rp-panel\s*\{[^}]*gap:\s*var\(--rp-form-gap\)/s)
  expect(css).not.toMatch(/margin:\s*-\d/)
})
```

Add token assertions for all exact values and a Tabs keyboard/overflow contract.

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/design-system/layout/PageLayout.test.tsx src/design-system/actions/Button.test.tsx src/design-system/navigation/Tabs.test.tsx`

- [ ] **Step 3: Implement the semantic geometry**

Use tokens in primitives, add `min-inline-size:0` to grid/flex children, keep nested surfaces visually separate, and reserve full-width primary buttons for `.rp-action-bar--mobile-primary`.

- [ ] **Step 4: Run tests and commit**

Run: `cd apps/web && npm test -- --run src/design-system/layout/PageLayout.test.tsx src/design-system/actions/Button.test.tsx src/design-system/navigation/Tabs.test.tsx`

```bash
git add apps/web/src/app/interface/interfaceTokens.css apps/web/src/app/interface/ClassicShell.css apps/web/src/design-system
git commit -m "refactor(ui): normalize classic geometry"
```

### Task 3: Rebuild work navigation and task action disclosures

**Files:**
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Modify: `apps/web/src/components/tracker/TaskCollaboration.tsx`
- Modify: `apps/web/src/components/tracker/TaskCollaboration.test.tsx`
- Modify: `apps/web/src/components/tracker/task-card.css`
- Modify: `apps/web/src/index.css`

**Interfaces:**
- Primary Tabs: `Задача / Проверка / Чат` when available.
- Secondary Tabs: `Открытые задачи / Закрытые задачи`.
- Write-off and handoff share one disclosure row and use secondary emphasis when closed, primary action inside when open.

- [ ] **Step 1: Add failing workbench geometry tests**

```tsx
it('uses one tab system and aligned disclosure actions', async () => {
  renderWorkbench({ client: apiClient() })
  expect(screen.getByRole('tablist', { name: 'Раздел задачи' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Списать запчасть' })).toHaveClass('rp-disclosure-action')
  expect(screen.getByRole('button', { name: 'Передать смену' })).toHaveClass('rp-disclosure-action')
})
```

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx src/components/tracker/TaskCollaboration.test.tsx`

- [ ] **Step 3: Replace ad-hoc buttons and CSS**

Use design-system `Tabs` for both levels, remove bare button selectors, render task header actions in a wrapping action bar, and make mobile disclosures one-column with 12 px internal gaps.

- [ ] **Step 4: Run tests and commit**

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx src/components/tracker/TaskCollaboration.test.tsx`

```bash
git add apps/web/src/domains/work/IssueWorkbench.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx apps/web/src/components/tracker/TaskCollaboration.tsx apps/web/src/components/tracker/TaskCollaboration.test.tsx apps/web/src/components/tracker/task-card.css apps/web/src/index.css
git commit -m "fix(ui): align task navigation and actions"
```

### Task 4: Repair mobile task, completion and inventory geometry

**Files:**
- Modify: `apps/web/src/components/tracker/IssueDetailPanel.tsx`
- Modify: `apps/web/src/components/tracker/IssueDetailPanel.test.tsx`
- Modify: `apps/web/src/domains/work/SubmitReviewForm.tsx`
- Modify: `apps/web/src/domains/work/SubmitReviewForm.test.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Modify: `apps/web/src/domains/inventory/InventoryReceiptsView.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryCountsView.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryManageView.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryManageView.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryReceiptsView.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryCountsView.test.tsx`

**Interfaces:**
- Below 720 px all completion and inventory forms are one column.
- File actions are `Сделать фото или выбрать файл`, `Заменить`, `Удалить` with consistent Button variants.
- Status chips remain inside cards and document editors keep 16 px edge padding.

- [ ] **Step 1: Add failing responsive component tests**

Assert semantic classes instead of jsdom pixel values: `rp-form-stack--mobile`, `rp-action-bar`, no inline width/grid styles, and every action uses `Button`.

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/components/tracker/IssueDetailPanel.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx`

- [ ] **Step 3: Implement responsive stacks and button hierarchy**

Remove two-column mobile completion fields, prevent title/action collision with `minmax(0,1fr)`, move badges into the card flow and apply the shared card/action tokens to inventory forms.

- [ ] **Step 4: Run tests and commit**

Run the same command as Step 2 and expect PASS.

```bash
git add apps/web/src/components/tracker apps/web/src/domains/inventory
git commit -m "fix(ui): repair mobile task and inventory layout"
```

### Task 5: Constrain diagnostic and long-content surfaces

**Files:**
- Modify: `apps/web/src/domains/diagnostics/UnknownDiagnosticInbox.tsx`
- Modify: `apps/web/src/domains/diagnostics/UnknownDiagnosticInbox.test.tsx`
- Modify: `apps/web/src/domains/diagnostics/diagnostics.css`
- Modify: `apps/web/src/domains/robots/DiagnosticEventDetails.tsx`
- Modify: `apps/web/src/domains/robots/robot-check.css`

**Interfaces:**
- Master/detail columns use `minmax(16rem, 28rem) minmax(0, 1fr)` on desktop and one column below 900 px.
- Raw payloads use bounded `<pre>` with `white-space:pre-wrap`, `overflow-wrap:anywhere`, `max-inline-size:100%`, `overflow:auto`.

- [ ] **Step 1: Add failing long-value tests**

```tsx
it('marks raw diagnostics as bounded content', async () => {
  renderUnknownInbox(longJsonFixture)
  expect(await screen.findByText(/FrequencyBelow/)).toHaveClass('rp-diagnostic-raw')
})
```

- [ ] **Step 2: Run, implement and rerun**

Run: `cd apps/web && npm test -- --run src/domains/diagnostics/UnknownDiagnosticInbox.test.tsx src/domains/robots/RobotCheckWorkspace.test.tsx`

Add the bounded classes, replace the giant full-width selected-action strip with a natural-width Button, and enforce `min-inline-size:0` on both columns.

Run the same command and expect PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/domains/diagnostics apps/web/src/domains/robots/DiagnosticEventDetails.tsx apps/web/src/domains/robots/robot-check.css
git commit -m "fix(ui): contain diagnostic payloads"
```

### Task 6: Add the all-route visual geometry gate

**Files:**
- Modify: `apps/web/e2e/operational/interface-geometry.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Modify: `apps/web/e2e/operational/routeFixtures.ts`
- Modify: `apps/web/src/index.css`
- Modify: `apps/web/src/app/shell/AppShell.css`
- Modify: `apps/web/src/domains/analytics/analytics.css`
- Modify: `apps/web/src/domains/campaigns/campaigns.css`
- Modify: `apps/web/src/domains/insights/insights.css`
- Modify: `apps/web/src/domains/management/management.css`
- Modify: `apps/web/src/domains/robots/robots.css`
- Modify: `apps/web/src/domains/shift/overview.css`
- Modify: `apps/web/src/domains/work/work.css`

**Interfaces:**
- Route matrix covers all manifest routes available to mechanic, operator, admin and royal at 390×844 and 1440×1000 in light/dark.
- Assertions: no document overflow, no control below 44 px, no text closer than 8 px to a bordered container, no overlapping interactive rectangles, no stale A selector.

- [ ] **Step 1: Extend route fixtures and assertions**

```ts
expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
expect(await page.locator('[data-interface="task-first"]').count()).toBe(0)
```

- [ ] **Step 2: Run only the targeted geometry projects**

Run: `cd apps/web && npx playwright test e2e/operational/interface-geometry.spec.ts e2e/operational/responsive-visual.spec.ts --project=chromium --workers=1`

Expected: initial failures identify remaining route-specific geometry defects.

- [ ] **Step 3: Fix each reported route using shared tokens**

Do not add route selectors to global CSS when a design-system primitive owns the geometry. Add a component test alongside every behaviour/markup change; pure CSS corrections remain covered by the geometry test.

- [ ] **Step 4: Rerun the same targeted E2E command and commit**

```bash
git add apps/web/src apps/web/e2e/operational
git commit -m "test(ui): enforce classic route geometry"
```

### Task 7: Verify cache continuity and cleanup

**Files:**
- Modify: `apps/web/src/lib/resource.ts`
- Modify: `apps/web/src/lib/resource.test.ts`
- Modify: `apps/web/src/lib/pollingCapacity.test.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryManageView.test.tsx`

**Interfaces:**
- Successful mutations retain the current screen and trigger background revalidation instead of clearing visible data.
- In-memory resources remain bounded at 128 entries; device resources use the existing bounded policy.
- Route exit removes listeners/timers and cancels unfinished owners without deleting settled cache entries.

- [ ] **Step 1: Add failing cache-continuity and cleanup tests**

```tsx
it('keeps settled work data across route remount while revalidating once', async () => {
  const first = renderWorkbench({ client: apiClient() })
  await screen.findByText('SDCFLEETOPS-1')
  first.unmount()
  renderWorkbench({ client: apiClient() })
  expect(screen.getByText('SDCFLEETOPS-1')).toBeVisible()
  expect(trackerIssues).toHaveBeenCalledTimes(2)
})
```

Add tests that repeated mount/unmount leaves no refresh subscriber, mutation success never calls `location.reload`, and an old principal cannot repopulate a new principal's cache.

- [ ] **Step 2: Run focused cache tests**

Run: `cd apps/web && npm test -- --run src/lib/resource.test.ts src/lib/pollingCapacity.test.tsx src/domains/work/IssueWorkbench.test.tsx src/domains/inventory/InventoryManageView.test.tsx`

- [ ] **Step 3: Fix confirmed ownership or invalidation defects**

Keep settled entries on `cancelPending`, use `revalidate` after mutations, invalidate only on permission/scope loss, and unregister both data and refresh subscriptions when their last consumer unmounts.

- [ ] **Step 4: Rerun and commit**

Run the same command as Step 2 and expect PASS.

```bash
git add apps/web/src/lib/resource.ts apps/web/src/lib/resource.test.ts apps/web/src/lib/pollingCapacity.test.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx apps/web/src/domains/inventory/InventoryManageView.test.tsx
git commit -m "fix(web): preserve bounded route caches"
```
