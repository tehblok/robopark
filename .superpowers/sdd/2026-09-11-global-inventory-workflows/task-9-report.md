# Task 9 report — scoped inventory export and task writeoff

## Scope

- Added authenticated CSV/Excel export with effective-permission gating, fixed mechanic/operator park scope, admin/royal single/all scope, safe Russian feedback, and stale-completion guards.
- Extended `/inventory` fleet scope: operator sees active fleet parks, admin/royal also retain inactive parks required by backend export, and mechanics remain restricted to assigned parks.
- Switched task writeoff selection to issue-park global catalog stock. The client submits the negative catalog adapter ID, preserves decimal-string int64 quantities, and continues to rely on the backend ownership and Tracker-comment contract.
- Mounted the export workflow in the existing URL-backed inventory tab and added compact mobile layout rules.

## TDD evidence

Initial RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryExportView.test.tsx src/domains/inventory/TaskPartsPanel.test.tsx src/app/park/ParkScopeProvider.test.tsx
Test Files 3 failed; 3 failed | 20 passed.
Expected causes: absent export view, legacy task picker, and inactive admin/royal parks filtered out.
```

Focused GREEN:

```text
cd apps/web && npm test -- --run src/domains/inventory src/app/park/ParkScopeProvider.test.tsx
Test Files 9 passed; Tests 112 passed.
```

## Implementation checks

- Export requires the effective `inventory.export` permission; role alone cannot restore a revoked grant.
- Only admin/royal receive the all-parks control, matching backend authorization. Their park list includes inactive parks; operator export stays fixed to the selected active park.
- Downloads use the existing credentials-included, content-type-checked API client. Success/error updates are owned by the current park/scope generation.
- Server export codes are mapped to Russian copy; raw integration values are not rendered.
- Task picker loads global catalog rows with the issue park ID, displays that park's quantity/location, and submits `-catalogPartId`. Quantity validation/comparison remains string/`BigInt` based through signed int64 max.
- Park and task loads reject stale completions after policy, park, or unmount changes.

## Final verification

```text
cd apps/web && npm test -- --run
129 files passed; 1891 tests passed.

cd apps/web && npm run build
exit 0; 2189 modules transformed (existing chunk-size warning only).

cd apps/web && npm run lint
exit 0; existing warnings only, none in Task 9 files.

cd apps/web && npm run check-nav
check-nav: ok (30 route ids).
```

The first full-suite attempt had one timing-sensitive failure in the unrelated analytics 401 retry test (`3` calls observed instead of `2`). Its isolated rerun passed, and the complete suite then passed 1891/1891 without any code change.

## Changed files

- `apps/web/src/domains/inventory/InventoryExportView.tsx`
- `apps/web/src/domains/inventory/InventoryExportView.test.tsx`
- `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- `apps/web/src/domains/inventory/TaskPartsPanel.test.tsx`
- `apps/web/src/domains/inventory/InventoryPage.tsx`
- `apps/web/src/app/park/ParkScopeProvider.tsx`
- `apps/web/src/app/park/ParkScopeProvider.test.tsx`
- `apps/web/src/i18n/ru.ts`
- `.superpowers/sdd/2026-09-11-global-inventory-workflows/task-9-report.md`

## Commit

`feat: export scoped inventory`

## Concerns

No Task 9 functional concerns. Repository lint and Vite chunk-size warnings are pre-existing and outside this task.

## Fix round 1/5

### RED

```text
cd apps/web && npm test -- --run src/domains/inventory/TaskPartsPanel.test.tsx src/domains/work/IssueWorkbench.test.tsx src/app/park/ParkScopeProvider.test.tsx
Test Files 3 failed; Tests 6 failed | 102 passed.
```

The failures reproduced URL park A being used for a task claimed in park B, no 201st catalog result/load-more, no unavailable-claim-park state, and inactive admin/royal parks remaining selected outside the export view. A separate RED proved that an unavailable tagged park incorrectly fell back to the URL park's queue.

### Changes

- Workbench derives the task park from authoritative issue tags and the mechanic's assigned parks, using queue only when the task has no tags. It passes that park ID to the task-parts owner and never substitutes the current URL park.
- Missing/unassigned claim parks render a clear error and a disabled writeoff action without issuing an inventory request.
- Task parts query only in-stock issue-park catalog rows, page in 200-row server batches, expose `Загрузить ещё`, deduplicate appended rows, and ignore late page results after a claim-park switch.
- Global admin/royal inactive parks are loaded only at `/inventory?view=export`. Changing to parts, manage, receipts, or counts reloads active parks, resolves the selection, and replaces the stale URL park. Export retains archived single-park and all-parks choices.

### GREEN and verification

```text
cd apps/web && npm test -- --run src/domains/inventory src/domains/work/IssueWorkbench.test.tsx src/app/park/ParkScopeProvider.test.tsx
10 files passed; 199 tests passed.

cd apps/web && npm test -- --run
129 files passed; 1898 tests passed.

cd apps/web && npm run build
exit 0; 2189 modules transformed (existing chunk-size warning only).

cd apps/web && npm run lint
exit 0; existing warnings only, none in fix-round files.

cd apps/web && npm run check-nav
check-nav: ok (30 route ids).
```

### Changed files

- `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- `apps/web/src/domains/inventory/TaskPartsPanel.test.tsx`
- `apps/web/src/domains/work/IssueWorkbench.tsx`
- `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- `apps/web/src/app/park/ParkScopeProvider.tsx`
- `apps/web/src/app/park/ParkScopeProvider.test.tsx`
- `.superpowers/sdd/2026-09-11-global-inventory-workflows/task-9-report.md`

### Concerns

None.
