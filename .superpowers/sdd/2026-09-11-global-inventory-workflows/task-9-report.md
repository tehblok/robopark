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
