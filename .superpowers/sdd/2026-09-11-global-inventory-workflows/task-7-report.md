# Task 7 report — mechanic-first parts search and management

## Scope

- Added the Task 7 parts search and management views, their focused tests, printable catalog labels, and compact responsive inventory styles.
- Stayed within the six files assigned by the brief plus this required report. `InventoryPage.tsx` remains the Task 6 composition point for the parent integration sequence.

## TDD evidence

Initial RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx
Test Files 2 failed (2)
Failure: InventoryPartsView and InventoryManageView did not exist.
```

Duplicate pagination RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryManageView.test.tsx
Test Files 1 failed (1); Tests 1 failed | 6 passed (7)
Failure: an existing duplicate outside the initial 200-row page could not open park settings.
```

Focused GREEN:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryPage.test.tsx
Test Files 3 passed (3); Tests 28 passed (28)
```

## Implementation

- Search field precedes filters and emits debounced, paginated `parkId/query/componentId/stockFilter/limit/offset` requests. A monotonically increasing request generation prevents older responses from replacing current results.
- Compact desktop/mobile cards show a thumbnail capped at 72 px, name, component/article, exact decimal-string quantity, and park location. Only the selected stock editor or selected printable label is mounted.
- Mechanics can create a global component/part for their assigned park and immediately initialize that park's stock. Operators receive only supplemental park-stock configuration.
- Admin and royal roles additionally receive global edit, archive, and merge controls. Mechanic/operator DOM contains no global destructive action.
- `inventory_article_exists` selects the structured `existing_part_id`, supplements the current page when necessary, opens that part's park settings, and leaves the creation draft intact.
- `InventoryLabels` now accepts the common catalog/legacy label shape and renders a useful fallback for missing locations.

## Verification

```text
cd apps/web && npm test
125 files passed; 1842 tests passed

cd apps/web && npm run build
exit 0; 2183 modules transformed

cd apps/web && npm run lint
exit 0; only pre-existing warnings outside Task 7 files

cd apps/web && npm run check-nav
check-nav: ok (30 route ids)
```

## Self-review

- Int64 quantities remain strings through input validation and `updateInventoryStock`; no `Number` conversion is used for quantities.
- Mutation coverage includes wrong search arguments, stale response replacement, missing photo bounds/card fields, multiple mounted mobile forms, leaked global actions, missing park-stock initialization, and lost duplicate draft/selection.
- The catalog photo flag currently reuses Task 6's authenticated `inventoryPartPhotoUrl(id)` interface; no separate catalog-photo URL exists in the supplied client contract.
