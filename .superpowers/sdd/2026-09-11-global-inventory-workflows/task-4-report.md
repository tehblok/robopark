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
- `all` and `component` creation scopes snapshot every active catalog part in scope, materializing
  and locking a zero park-stock row when needed. Empty valid scopes remain valid.
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
  the catalog part. Snapshot materialization guarantees that initially absent zero stock has the
  same authorized correction path after archival as pre-existing zero stock.
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

## Fix round 1/5

### Review findings and RED evidence

All three Important findings and the normalized-name update coverage gap were reproduced before
their corresponding production changes.

Combined initial command:

`cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py::test_count_materializes_missing_zero_stock_before_archive_and_authorizes_its_line_only tests/test_inventory_counts.py::test_count_accepts_quantities_above_one_million_within_database_integer_capacity tests/test_inventory_counts.py::test_count_name_search_uses_persisted_python_casefold_on_all_dialects tests/test_models_migration.py::test_inventory_upgrade_preserves_existing_data -q`

Result: `4 failed, 1 warning in 0.94s`.

- Count creation left both scoped, initially absent park-stock rows missing.
- Pydantic rejected `1_000_001` and `2_000_000_000` with HTTP 422.
- `InventoryCount` had no `normalized_name`, and migration metadata lacked the field/index.

The ORM update invariant received a separate test before its model validator:

`cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py::test_count_name_normalization_tracks_orm_updates -q`

Result: `1 failed, 1 warning in 0.33s`; after changing `name`, normalized storage remained
`original` instead of `weisse οσ`.

### Fixes

- Count creation now takes the shared alias-graph lock and calls the canonical stock writer for
  every scoped active part in ascending part-ID order. This atomically materializes and locks zero
  rows before snapshot lines are persisted. A missing-zero line can therefore be counted after its
  part is archived, while no unrelated archived movement is enabled; the test proves retry
  idempotency and a zero unrelated stock with no movement.
- Removed the arbitrary one-million cap from both request validation and service validation.
  Non-negative values within database integer capacity are accepted; the regression covers an
  unchanged `1_000_001` line and a `2_000_000_000` positive adjustment.
- Added required `InventoryCount.normalized_name` (`String(384)`) and the composite
  `(park_id, normalized_name)` index to ORM metadata and the unshipped `0026` migration. The wider
  field accommodates Python casefold expansions for a 128-character display name.
- The ORM `name` validator stores exact Python `casefold()` output for both inserts and updates.
  Both SQLite and PostgreSQL now query the persisted normalized field with the same escaped,
  casefolded pattern; no locale-sensitive `lower`, `ILIKE`, or partial alphabet translation remains.
- No data-backfill statement is needed inside `0026`: `inventory_counts` is first created by that
  same unshipped revision, so there are no pre-revision count rows. Upgrade metadata and downgrade
  roundtrip remain covered by `test_inventory_upgrade_preserves_existing_data`.

### GREEN and final verification

- Individual zero-stock/archive regression: `1 passed, 1 warning in 1.00s`.
- Individual large-quantity regression: `1 passed, 1 warning in 0.29s`.
- Persisted search plus migration metadata: `2 passed, 1 warning in 0.52s`.
- ORM update normalization: `1 passed, 1 warning in 0.24s`.
- Focused counts/receipts/migration/models/identity:
  `94 passed, 21 warnings in 18.05s`.
- Full API suite: `1437 passed, 21 warnings in 240.45s (0:04:00)`.
- Final focused rerun: `94 passed, 21 warnings in 21.46s`.
- Final Ruff check: `All checks passed!`; Ruff format check: `6 files already formatted`;
  `git diff --check` exited zero.

### Self-review and concerns

- Snapshot stock creation, count lines, and audit share one transaction; any later failure rolls
  back all three. The advisory lock precedes catalog resolution and sorted stock locking.
- The archived correction remains source-bounded by immutable count line IDs and stable
  `<count-id>:<line-id>` movement identities; ordinary archived-part movements still use the
  default rejecting path.
- Exact casefold behavior is tested with German sharp-s and Greek final sigma, and compiled
  PostgreSQL SQL is asserted to use only `inventory_counts.normalized_name`.
- No correctness blocker remains. Live PostgreSQL contention remains unavailable; locking and SQL
  shape are verified through the existing dialect-level protocol tests.

## Fix round 2/5

### Review findings and RED evidence

Both Important findings were reproduced before production changes with:

`cd apps/api && .venv/bin/pytest tests/test_inventory_counts.py::test_count_list_has_unicode_search_stable_pagination_and_one_line_query tests/test_inventory_counts.py::test_count_name_search_uses_persisted_python_casefold_on_all_dialects tests/test_inventory_counts.py::test_count_actual_rejects_values_above_signed_int64_without_mutating_draft tests/test_inventory_counts.py::test_receipt_post_rejects_int64_stock_overflow_and_rolls_back_every_line tests/test_models_migration.py::test_global_inventory_accumulators_compile_as_postgresql_bigint -q`

Result: `5 failed, 1 warning in 2.71s`.

- Count search still used a leading-wildcard `LIKE`, kept recent-first ordering, and had no
  covering composite indexes for either the searched or unfiltered page.
- `2**63` reached SQLite from both count line updates and receipt posting as an uncaught
  `OverflowError`; an overflowing second receipt line could therefore fail at movement insert.
- Every new workflow accumulator still compiled as PostgreSQL `INTEGER`, including park stock,
  receipt/count quantities, and movement balances.

### Fixes

- Defined shared signed 64-bit inventory bounds (`-2**63` through `2**63 - 1`). All new global
  inventory accumulators use SQLAlchemy `BigInteger`; migration `0026` creates new values as
  `BIGINT` and widens the pre-existing movement delta/final-balance columns during upgrade.
- Count actual quantities accept `0..INT64_MAX` in both Pydantic and the service boundary. Direct
  service calls outside that range raise `InventoryValidation`; the router maps it to controlled
  HTTP 422 instead of leaking a driver error. Values above one million remain supported.
- Receipt normalization validates each quantity and the sum of duplicate lines against int64.
  Stock mutation validates the input delta and current balance, then rejects positive overflow or
  negative underflow before persistence. SQLite's conditional update repeats both limits inside
  the atomic statement; the regression proves an earlier line, its movement, and document status
  all roll back when a later line overflows.
- Count `q` semantics are now documented and tested as Unicode Python-casefolded **prefix** search.
  The query uses explicit `normalized_name >= prefix` and `< successor` range predicates, never
  locale-sensitive `LIKE`, `lower`, or `ILIKE`. Search pages sort by normalized name, creation,
  and ID; unfiltered pages retain creation/ID descending order.
- Added `(park_id, normalized_name, created_at, id)` and `(park_id, created_at, id)` indexes to the
  model and migration. SQLite `EXPLAIN QUERY PLAN` regressions prove the representative page scans
  the corresponding composite index without a temporary sort; compiled PostgreSQL SQL asserts the
  same range-predicate/order shape.

### GREEN and verification evidence

- Selected RED set plus migration upgrade after implementation:
  `6 passed, 1 warning in 1.15s` (an intermediate metadata comparison caught two accidental legacy
  field widenings; restoring those pre-global fields yielded `2 passed, 1 warning in 0.32s`).
- Focused counts/receipts/catalog/identity/legacy/migration/models:
  `126 passed, 21 warnings in 27.37s`.
- Full API suite: `1440 passed, 21 warnings in 255.26s (0:04:15)`.
- Final focused rerun after self-review: `126 passed, 21 warnings in 25.43s`.
- Ruff check: `All checks passed!`; Ruff formatting reformatted two owned files and subsequent
  format check reported `9 files already formatted`; `git diff --check` exited zero.

### Self-review and concerns

- Idempotency lookup deliberately precedes new balance validation: replaying an already-written
  sourced movement returns that immutable event without trying to apply its delta again.
- Count differences remain signed-int64-safe because both expected and actual balances are bounded
  to `0..INT64_MAX`. Zero deltas retain their count lines and still create no movement.
- The query-plan test covers SQLite and SQL compilation covers PostgreSQL. A live PostgreSQL
  planner/overflow environment was not available; there is no known correctness blocker.
