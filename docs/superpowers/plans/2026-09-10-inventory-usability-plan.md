# Inventory Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a compact component-filtered inventory catalog, batch A4 label printing, and an obvious task write-off control for the mechanic who owns the task.

**Architecture:** Keep the existing inventory API and write-off transaction unchanged. Add small pure view helpers for filtering and ownership, render one reusable print-label collection, and surface the existing `TaskPartsPanel` inside the main task tab when the current mechanic owns the issue.

**Tech Stack:** React 19, TypeScript 6, Vitest, Testing Library, CSS print media.

**Spec:** `docs/superpowers/specs/2026-09-10-inventory-usability-and-technical-cleanup-design.md`

## Global Constraints

- Business data and inventory photos are never deleted by this work.
- A mechanic may write off inventory only from an assigned task; the API remains the final authorization boundary.
- Admin, royal, and operator inventory editing remains available only through the inventory page.
- Printing uses browser-native A4 print CSS and adds no dependency.

---

### Task 1: Compact filtered inventory catalog

**Files:**
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`

**Interfaces:**
- Consumes: `InventoryOverview.components` and existing inventory photo URL functions.
- Produces: `visibleComponents(data, componentId): InventoryComponent[]` and a labeled `Компонента` selector whose empty value means all components.

- [ ] **Step 1: Write the failing catalog test**

Add a second component fixture and assert that the default view contains both groups, selecting its numeric id hides the other group, and every rendered part image uses the compact `inventory-part__photo` element. The break caught is a catalog that ignores the component filter or falls back to large media.

```tsx
expect(await screen.findByRole('heading', { name: 'Подвязка' })).toBeInTheDocument()
expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')
expect(screen.queryByRole('heading', { name: 'Подвязка' })).not.toBeInTheDocument()
expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm test -- InventoryPage.test.tsx` from `apps/web`.

Expected: FAIL because the page has no catalog component selector.

- [ ] **Step 3: Implement the filtered compact catalog**

Add `componentId` state with `"all" | number`, derive visible groups without another API call, render the filter before the group list, and retain each component as a collapsible `Panel`. Change card CSS to a responsive grid with a 4rem square thumbnail on desktop and mobile; do not enlarge photos on narrow screens.

```ts
const visibleComponents = componentId === 'all'
  ? data.components
  : data.components.filter(component => component.id === componentId)
```

- [ ] **Step 4: Run the catalog test and verify GREEN**

Run: `npm test -- InventoryPage.test.tsx` from `apps/web`.

Expected: PASS with no console warnings.

- [ ] **Step 5: Commit the catalog change**

```bash
git add apps/web/src/domains/inventory/InventoryPage.tsx apps/web/src/domains/inventory/inventory.css apps/web/src/domains/inventory/InventoryPage.test.tsx
git commit -m "feat(inventory): add compact component catalog"
```

### Task 2: Batch A4 label printing

**Files:**
- Create: `apps/web/src/domains/inventory/InventoryLabels.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`

**Interfaces:**
- Consumes: visible `InventoryPart[]` from Task 1.
- Produces: `InventoryLabels({ parts }: { parts: InventoryPart[] })` and print selection state containing one part or the whole visible selection.

- [ ] **Step 1: Write failing batch-print tests**

Assert that `Печать этикеток` creates one printable label per visible part, that filtering limits the printed labels to the selected component, and that the existing single-part action creates exactly one label. The break caught is printing hidden components or rendering only the last part.

```tsx
await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))
expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(2)
expect(document.querySelector('.inventory-print-sheet')).toHaveTextContent('TY-001')
expect(window.print).toHaveBeenCalledTimes(1)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `npm test -- InventoryPage.test.tsx` from `apps/web`.

Expected: FAIL because no batch print action or print sheet exists.

- [ ] **Step 3: Implement reusable labels and print state**

Flatten active parts from `visibleComponents`, pass them to `InventoryLabels`, and schedule `window.print()` only after setting a non-empty selection. Keep the per-card print action using the same component.

```tsx
export function InventoryLabels({ parts }: { parts: InventoryPart[] }) {
  return <section className="inventory-print-sheet" aria-hidden="true">
    {parts.map(part => <article className="inventory-print-label" key={part.id}>
      <strong>{part.name}</strong><span>Артикул: {part.article}</span><b>{part.location}</b>
    </article>)}
  </section>
}
```

Add `@page { size: A4; margin: 10mm }`; show only `.inventory-print-sheet` under `@media print`; use a two-column grid, fixed label boundaries, and `break-inside: avoid` so overflow naturally continues to later sheets.

- [ ] **Step 4: Run the tests and build**

Run: `npm test -- InventoryPage.test.tsx && npm run build` from `apps/web`.

Expected: tests PASS and production build exits 0.

- [ ] **Step 5: Commit label printing**

```bash
git add apps/web/src/domains/inventory/InventoryLabels.tsx apps/web/src/domains/inventory/InventoryPage.tsx apps/web/src/domains/inventory/inventory.css apps/web/src/domains/inventory/InventoryPage.test.tsx
git commit -m "feat(inventory): print labels in batches"
```

### Task 3: Write-off control in the main task tab

**Files:**
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Test: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`

**Interfaces:**
- Consumes: `TrackerIssueDetail.assignee`, `User.tracker_login || User.username`, and existing `TaskPartsPanel` API.
- Produces: `mechanicOwnsIssue(user, issue): boolean` and an `Использовать запчасть` collapsible block in the main task tab.

- [ ] **Step 1: Write failing ownership and visibility tests**

Render the workbench with a mechanic whose normalized login matches the selected issue assignee and assert the main `Задача` panel exposes `Использовать запчасть`. Render an unassigned, foreign-assigned, and operator case and assert the control is absent. The break caught is exposing write-off outside an owned task or hiding it behind the old tab.

```tsx
const mechanic = { ...user, role: 'mechanic' as const, username: 'mech', tracker_login: 'Mech.Login' }
const owned = { ...issue, assignee: { display: 'Mechanic', login: 'mech.login' } }
expect(await screen.findByText('Использовать запчасть')).toBeInTheDocument()
expect(screen.queryByRole('tab', { name: 'Запчасти' })).not.toBeInTheDocument()
```

- [ ] **Step 2: Run the workbench test and verify RED**

Run: `npm test -- IssueWorkbench.test.tsx` from `apps/web`.

Expected: FAIL because write-off exists only in a separate mechanic tab and does not check ownership in the view.

- [ ] **Step 3: Implement the owned-task entry point**

Compare trimmed logins case-insensitively, remove the separate `parts` tab/state branch, and render a collapsible `Panel` after `IssueDetailPanel` only when ownership is true. Mount `TaskPartsPanel` only when expanded so it does not fetch inventory while hidden.

```ts
function mechanicOwnsIssue(user: User, issue: TrackerIssueDetail): boolean {
  const expected = (user.tracker_login || user.username).trim().toLocaleLowerCase()
  return user.role === 'mechanic'
    && Boolean(expected)
    && issue.assignee?.login?.trim().toLocaleLowerCase() === expected
}
```

- [ ] **Step 4: Make the task selector compact and verify GREEN**

Reduce task preview photos to 4rem, retain component/part/quantity validation, and reset invalid part and quantity selections after inventory refresh. Run: `npm test -- IssueWorkbench.test.tsx InventoryPage.test.tsx` from `apps/web`.

Expected: both files PASS with no warnings.

- [ ] **Step 5: Commit task write-off UX**

```bash
git add apps/web/src/domains/work/IssueWorkbench.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx apps/web/src/domains/inventory/TaskPartsPanel.tsx apps/web/src/domains/inventory/inventory.css apps/web/src/domains/inventory/InventoryPage.test.tsx
git commit -m "fix(inventory): expose writeoff in owned tasks"
```

### Task 4: Inventory regression verification

**Files:**
- Verify only; modify failures only within files listed by Tasks 1–3.

**Interfaces:**
- Consumes: completed inventory UI and unchanged API contract.
- Produces: fresh verification evidence.

- [ ] **Step 1: Run focused web tests**

Run: `npm test -- InventoryPage.test.tsx IssueWorkbench.test.tsx` from `apps/web`.

Expected: all selected tests PASS.

- [ ] **Step 2: Run the full web test suite**

Run: `npm test` from `apps/web`.

Expected: all tests PASS.

- [ ] **Step 3: Run lint and production build**

Run: `npm run lint && npm run build` from `apps/web`.

Expected: both commands exit 0.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check && git status --short`.

Expected: no whitespace errors and only planned files are modified.
