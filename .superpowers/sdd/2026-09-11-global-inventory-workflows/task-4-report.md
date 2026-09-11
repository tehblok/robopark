# Task 4 report — inventory count documents

## Scope and baseline

- Base HEAD: `bfaf000cc912558d4cd74f15ba3b58c87e5f7776`.
- Isolated worktree: `.worktrees/global-inventory-workflows`.
- Owned files:
  - `apps/api/src/robopark_api/services/inventory_counts.py`
  - `apps/api/src/robopark_api/inventory_schemas.py`
  - `apps/api/src/robopark_api/routers/inventory.py`
  - `apps/api/tests/test_inventory_counts.py`
  - `.superpowers/sdd/2026-09-11-global-inventory-workflows/task-4-report.md`
- Baseline command:
  `cd apps/api && .venv/bin/pytest tests/test_inventory_receipts.py tests/test_inventory_catalog.py tests/test_inventory_identity.py tests/test_inventory.py -q`
- Baseline result: `71 passed, 1 warning in 12.87s`.

## TDD RED evidence

The count tests were written before any production count service, schemas, or routes.

Command:

`cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py -q`

Result: `8 failed, 1 warning in 1.67s`.

- Lifecycle, stale-conflict, validation, access, listing, and historical-alias scenarios reached
  exact `404 Not Found` count endpoints (dependent assertions then lacked a count ID).
- The two direct service/concurrency tests failed to import the intentionally absent
  `robopark_api.services.inventory_counts` module.

This demonstrated that the tests exercised the missing Task 4 boundary, rather than already
existing behavior.

## Implementation

- Added create/update/list/post/cancel services and the five requested park-scoped API routes.
- `all` and `component` creation scopes snapshot every active catalog part in scope, using the
  park stock quantity or zero when the park has no stock row. Empty valid scopes remain valid.
- Draft line updates accept non-negative actual quantities, calculate and persist differences,
  reject duplicate or out-of-document part IDs, and require every line to be counted before post.
- Lists return `{items, limit, offset, total}`, sort by creation and ID descending, load page lines
  in one query, and perform escaped Unicode case-insensitive name search on SQLite/PostgreSQL.
- Posting requires the document-post capability and park access, then follows the bounded Task 3
  lock protocol: shared alias advisory lock, document lock/SQLite writer reservation, alias
  resolution, and canonical stock locks in ascending part-ID order.
- All stale canonical stock groups are collected before the first movement. HTTP 409 returns
  `inventory_count_stale` plus every expected/current conflict; rollback leaves stock, movements,
  document status, and posted audit unchanged.
- Non-zero per-line signed deltas use `kind="adjustment"`, `source_kind="count"`, and stable
  `<count-id>:<line-id>` source IDs. Positive deltas are applied before negative deltas so alias
  consolidation cannot create a transient negative balance. Zero-delta lines stay in the document
  and create no movement.
- A posted retry returns the existing posted document without movement or audit duplication.
- Historical source IDs survive merges. Parts archived after snapshot are resolved deliberately
  with the archived/history path; an existing historical stock can be adjusted without restoring
  the catalog part, while an absent archived zero/zero line remains a no-op rather than creating
  stock.
- Create, update, post, and cancel mutations write attributed, park-scoped audit records in the
  same transaction. Only drafts can be edited or cancelled.

## GREEN and verification evidence

- Required focused counts/receipts command:
  `cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py tests/test_inventory_receipts.py -q`
  -> `29 passed, 1 warning in 5.07s`.
- Extended counts/receipts/catalog/identity/legacy/RBAC command:
  `cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py tests/test_inventory_receipts.py tests/test_inventory_catalog.py tests/test_inventory_identity.py tests/test_inventory.py tests/test_rbac_seed.py -q`
  -> `85 passed, 1 warning in 14.11s`.
- Full API command:
  `cd apps/api && .venv/bin/pytest -q`
  -> `1433 passed, 21 warnings in 231.96s (0:03:51)`.
- Final focused rerun: `29 passed, 1 warning in 8.92s`.
- Final Ruff check: `All checks passed!`; Ruff format check: `4 files already formatted`;
  `git diff --check` exited zero.

## Self-review and concerns

- Mutation review confirms tests fail for a missing snapshot, wrong signed delta, movement on zero,
  missing idempotency guard, partial stale mutation, incomplete conflict collection, unsorted stock
  locks, missing permission/scope checks, and locale-sensitive Cyrillic search.
- Two independent SQLite sessions post one count concurrently and prove one stock delta and one
  movement. PostgreSQL alias coordination reuses the Task 3/Task 2 advisory-lock function already
  covered by dialect-level tests; a live PostgreSQL contention environment was not available.
- No known correctness blocker remains. The 21 full-suite warnings are the existing Starlette
  TestClient warning and SQLite datetime-adapter warnings from migration tests.
