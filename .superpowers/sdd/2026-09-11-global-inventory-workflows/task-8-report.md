# Task 8 report — web receipts and inventory counts

## Scope

- Added receipt and count list/detail workflows to the existing URL-backed inventory tabs.
- Kept document IDs and statuses authoritative from API responses; local state contains only editable, unsent fields.
- Added compact desktop master/detail and phone list/editor-with-back layouts.

## TDD evidence

Initial RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx
Test Files 2 failed (2); both failed to resolve the absent view modules.
```

Shell integration RED:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx
Tests 2 failed | 5 passed; receipts/counts still mounted placeholders.
```

Focused GREEN:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryTabs.test.tsx src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx src/domains/inventory/InventoryPage.test.tsx
Test Files 4 passed; Tests 28 passed.
```

Round 1 review regressions covered receipt hydration/retry, catalog pagination and aliases,
component scopes, permissions, list retry, validation timing, and mobile pending guards:

```text
cd apps/web && npm test -- --run src/domains/inventory/InventoryReceiptsView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx src/domains/inventory/InventoryTabs.test.tsx
Test Files 3 passed; Tests 28 passed.

cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py -q
18 passed.
```

## Implementation

- Receipt creation searches the scoped server catalog, adds lines, combines duplicate part IDs, validates digit-only positive int64 strings, confirms posting, and supports draft cancellation and posted reversal where the API exposes them.
- Opening a receipt hydrates all server draft lines. A newly created draft is selected before posting, so a failed/lost post response retries the same server ID without creating or patching another receipt.
- Count creation uses the server-produced scope lines, retains actual-quantity inputs across 409 stale conflicts, shows expected/current conflict values inline, computes differences with `BigInt`, and offers an explicit retry.
- Count line identities are resolved through all required catalog pages. Stale conflicts now carry their original count-line and catalog-part IDs, so merged aliases map back to every affected input.
- Component scopes load the complete paginated component catalog and submit the exact selected component scope.
- Both document lists use server query/offset pagination and generation guards for list, catalog, park, and mutation responses.
- A synchronous pending ref plus disabled confirmation controls prevents duplicate mutations before React can render pending state.
- Effective permissions hide creation, editing, posting, cancellation, and reversal controls independently of role; list/detail reading remains available.
- List failures have visible retry actions, validation alerts appear only after fields are touched, reversal reasons reset at workflow boundaries, and Back is guarded while mutations are pending.
- Successful mutations refresh their document list and notify `InventoryPage`, which refreshes the overview KPIs; subsequent catalog searches reload current stock data.
- The active tab mounts exactly one workflow. Desktop shows list/detail columns; phone hides the list while the single editor is active and exposes Back.

## Verification

```text
cd apps/web && npm test
127 files passed; 1860 tests passed.

cd apps/web && npm run build
exit 0; 2188 modules transformed; existing chunk-size warning only.

cd apps/web && npm run lint
exit 0; existing warnings outside Task 8 files only.

cd apps/web && npm run check-nav
check-nav: ok (30 route ids).

cd apps/api && .venv/bin/pytest
1468 passed, 21 warnings in 228.70s.

cd apps/api && .venv/bin/ruff check src/robopark_api/services/inventory_counts.py tests/test_inventory_counts.py
All checks passed.

cd apps/api && .venv/bin/ruff format --check src/robopark_api/services/inventory_counts.py tests/test_inventory_counts.py
2 files already formatted.
```

## Self-review

- No quantity is converted through JavaScript `Number`; validation caps values at signed int64 max and differences/duplicate sums use `BigInt`.
- 409 count conflicts do not replace the selected authoritative document or clear unsent actual quantities.
- Stale responses cannot update a later park generation.
- The backend extension is limited to the stale-count conflict payload and its API regressions; no success contract or persistence schema changed.

## Concerns

- None. Existing Vite chunk-size and repository lint warnings are unchanged and outside Task 8.
