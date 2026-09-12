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

## Implementation

- Receipt creation searches the scoped server catalog, adds lines, combines duplicate part IDs, validates digit-only positive int64 strings, confirms posting, and supports draft cancellation and posted reversal where the API exposes them.
- Count creation uses the server-produced scope lines, retains actual-quantity inputs across 409 stale conflicts, shows expected/current conflict values inline, computes differences with `BigInt`, and offers an explicit retry.
- Both document lists use server query/offset pagination and generation guards for list, catalog, park, and mutation responses.
- A synchronous pending ref plus disabled confirmation controls prevents duplicate mutations before React can render pending state.
- Successful mutations refresh their document list and notify `InventoryPage`, which refreshes the overview KPIs; subsequent catalog searches reload current stock data.
- The active tab mounts exactly one workflow. Desktop shows list/detail columns; phone hides the list while the single editor is active and exposes Back.

## Verification

```text
cd apps/web && npm test
127 files passed; 1853 tests passed.

cd apps/web && npm run build
exit 0; 2188 modules transformed; existing chunk-size warning only.

cd apps/web && npm run lint
exit 0; existing warnings outside Task 8 files only.

cd apps/web && npm run check-nav
check-nav: ok (30 route ids).
```

## Self-review

- No quantity is converted through JavaScript `Number`; validation caps values at signed int64 max and differences/duplicate sums use `BigInt`.
- 409 count conflicts do not replace the selected authoritative document or clear unsent actual quantities.
- Stale responses cannot update a later park generation.
- Changes are limited to the six brief-owned code/test files, the focused `InventoryPage.test.tsx` integration coverage, and this required report.

## Concerns

- None. Existing Vite chunk-size and repository lint warnings are unchanged and outside Task 8.
