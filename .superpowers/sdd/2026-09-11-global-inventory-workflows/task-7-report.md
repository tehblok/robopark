# Task 7 report — mechanic-first parts search and management

## Scope

- Mounted the Task 7 parts and manage views in the real inventory shell while keeping park identity, KPI strip, URL tabs, active-only panels, and non-blocking overview failures.
- Added scoped component metadata and exact catalog-part reads used by filters and duplicate recovery.
- Hardened pagination, park-switch isolation, archive confirmation, catalog photo identity, and signed-int64 input validation.

## TDD evidence

Initial review RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryPage.test.tsx
Test Files 3 failed (3); Tests 9 failed | 24 passed
Failures covered shell mounting, negative catalog photo IDs, independent component metadata,
server paging, stale park mutations, exact duplicate recovery, archive confirmation,
and INT64_MAX validation.
```

Focused GREEN:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryTabs.test.tsx src/domains/inventory/InventoryPartsView.test.tsx src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryPage.test.tsx
Test Files 4 passed (4); Tests 37 passed (37)
```

Backend GREEN:

```text
cd apps/api && .venv/bin/pytest tests/test_inventory_catalog.py -q
19 passed, 1 third-party deprecation warning
```

## Implementation

- Search field precedes filters and emits debounced, server-paginated requests. Separate request generations reject stale search and stock-mutation responses.
- Stock responses update local state only when both the captured park and returned `park_id` still match the active park.
- Compact catalog cards use the negative catalog adapter ID for authenticated photos, preserving the legacy positive-ID namespace.
- Component filters load from an independent paginated catalog endpoint rather than the current results page.
- Manage source selection and merge targets both search and page on the server beyond the first 200 rows.
- Duplicate article recovery fetches `existing_part_id` directly, mounts settings only after a truthful match, and preserves the creation draft.
- Global archive requires the design-system confirmation dialog; mechanic/operator DOM still contains no global destructive controls.
- All quantity inputs reject non-digits and values above `9223372036854775807` inline without issuing an API request.

## Verification

```text
cd apps/web && npm test
125 files passed; 1834 tests passed

cd apps/web && npm run build
exit 0; 2185 modules transformed (pre-existing chunk-size warning)

cd apps/web && npm run lint
exit 0; pre-existing warnings only, none in Task 7 files

cd apps/web && npm run check-nav
check-nav: ok (30 route ids)

cd apps/api && .venv/bin/ruff check src/robopark_api/inventory_schemas.py src/robopark_api/routers/inventory.py src/robopark_api/services/inventory_catalog.py tests/test_inventory_catalog.py
All checks passed
```

## Self-review

- Quantities remain decimal strings in the web client and are never converted through JavaScript `Number`.
- New backend reads enforce the same park access check as catalog search; focused tests cover allowed and foreign-park requests.
- The full web suite has no skipped Task 7 tests.
