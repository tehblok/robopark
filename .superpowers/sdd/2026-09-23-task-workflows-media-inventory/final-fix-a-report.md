# Final review Fix A — backend consistency report

## Result

All four backend P1 findings are addressed without frontend changes.

- Pending `TrackerClaim` rows remain reservations. Review submission and task
  stock write-off now require the current mechanic's claim to be `active` and
  return stable `tracker_issue_claim_not_active` conflicts before persisting
  review, attachment, movement, comment or audit effects.
- Terminal ordered-start failures keep the linked pending reservation. A second
  mechanic cannot claim it, manager `retry-now` retries the failed chain, and a
  successful retried `start` activates the original mechanic.
- Permanent deletion of a posted count locks the document and its adjustments,
  reverses grouped count deltas into the current canonical park stock, deletes
  the adjustment ledger and document in the same transaction, and rolls all of
  it back together on failure.
- Managed inventory-photo replacement/removal/permanent deletion enqueue an
  `inventory_photo_cleanup` row before the business commit. Cleanup is
  idempotent, refuses to unlink a currently referenced key, runs immediately
  after commit and is retried by the existing cache-cleanup job after restart.
  An unlink failure keeps the durable row and returns HTTP 202; successful
  immediate cleanup preserves the previous 200/204 contracts.

## RED

Command:

```text
cd apps/api
.venv/bin/pytest -q \
  tests/test_task_lifecycle.py::test_submit_review_requires_active_claim_before_persisting_review_effects \
  tests/test_inventory.py::test_task_writeoff_requires_active_claim_before_persisting_stock_effects \
  tests/test_tracker_outbox.py::test_terminal_start_failure_remains_reserved_and_admin_retry_activates_original_owner \
  tests/test_inventory_counts.py::test_permanent_delete_posted_count_reverses_stock_and_removes_adjustment \
  tests/test_inventory_catalog.py::test_catalog_photo_replace_persists_retryable_cleanup_after_unlink_failure
```

Observed: 5 failed for the intended missing behavior: pending operations were
accepted, terminal failure deleted the reservation, count deletion left stock
at 7, and unlink failure had no durable accepted-cleanup path. The review test
continued as far as staged-file creation because the missing active-state gate
did not stop it.

## GREEN and verification

Focused new regressions after implementation:

```text
.venv/bin/pytest -q <8 focused Fix A regression/migration nodes>
```

Result: `8 passed, 1 warning`.

Final focused API regression command:

```text
.venv/bin/pytest -q \
  tests/test_task_lifecycle.py \
  tests/test_tracker_outbox.py \
  tests/test_inventory.py \
  tests/test_inventory_counts.py \
  tests/test_inventory_catalog.py \
  tests/test_models_migration.py
```

Result: `215 passed, 1 warning in 54.22s`. The warning is the pre-existing
Starlette `httpx` deprecation warning.

Ruff command:

```text
.venv/bin/ruff check --no-cache \
  src/robopark_api/models.py \
  src/robopark_api/routers/inventory.py \
  src/robopark_api/services/cache_cleanup.py \
  src/robopark_api/services/inventory.py \
  src/robopark_api/services/inventory_catalog.py \
  src/robopark_api/services/inventory_deletion.py \
  src/robopark_api/services/inventory_photo_cleanup.py \
  src/robopark_api/services/task_lifecycle.py \
  src/robopark_api/services/tracker_claims.py \
  src/robopark_api/services/tracker_outbox.py \
  alembic/versions/0038_inventory_photo_cleanup.py \
  tests/test_inventory.py tests/test_inventory_catalog.py \
  tests/test_inventory_counts.py tests/test_models_migration.py \
  tests/test_task_lifecycle.py tests/test_tracker_outbox.py
```

Result: `All checks passed!`.

`git diff --check` also passed.

## Migration and compatibility reasoning

- `0038_inventory_photo_cleanup` is additive: one new table with a
  `storage_key` primary key, attempt/error diagnostics and timestamps. It does
  not rewrite existing catalog, stock, movement or claim rows and requires no
  backfill.
- Existing databases retain the `0037` claim backfill (`active` for legacy
  claims). Only newly created ordered claims are pending until `start`
  succeeds.
- Cleanup row presence is the pending state. Missing files are successful
  idempotent cleanup; a stale marker for a newly/currently referenced key is
  completed without unlinking the blob.
- Business rows are never rolled back after a post-commit unlink failure. The
  response changes to 202 only when durable cleanup remains accepted/pending;
  response bodies are unchanged.
- Count reversal is park-scoped and uses the current canonical part identity.
  If later stock makes reversal invalid, deletion fails and rollback preserves
  the count, its ledger and stock together.
- No external Tracker history is deleted or rewritten.

## Changed files

- Migration/model: `apps/api/alembic/versions/0038_inventory_photo_cleanup.py`,
  `apps/api/src/robopark_api/models.py`.
- Claim/retry consistency: `services/tracker_claims.py`,
  `services/task_lifecycle.py`, `services/tracker_outbox.py`,
  `services/inventory.py`.
- Inventory deletion/photo cleanup: `services/inventory_catalog.py`,
  `services/inventory_deletion.py`, `services/inventory_photo_cleanup.py`,
  `services/cache_cleanup.py`, `routers/inventory.py`.
- Focused regressions/migration coverage: `tests/test_task_lifecycle.py`,
  `tests/test_tracker_outbox.py`, `tests/test_inventory.py`,
  `tests/test_inventory_counts.py`, `tests/test_inventory_catalog.py`,
  `tests/test_models_migration.py`.

One existing concurrent-claim test was made deterministic by capturing the
scalar park id before entering worker threads instead of lazy-loading a shared
ORM fixture from a worker thread.

## Follow-up review blockers

The release-blocking follow-up covered ambiguous commit outcomes and concurrent
catalog-photo mutations/deletions. This was delivered as a separate focused
change without altering the accepted API contract.

### RED

Focused regressions were added for:

- a commit wrapper that performs the real commit and then raises (lost ACK);
- a true commit failure whose newly stored blob must be recovered durably;
- `FOR UPDATE` selection before replace/remove photo mutations;
- catalog-row locking before permanent part/component deletion snapshots and
  bulk deletes.

The initial focused run produced `4 failed, 1 passed`: the lost-ACK path
unlinked a blob already referenced by the committed database row, catalog
mutations emitted no row lock, and permanent deletes did not acquire catalog
writer locks. The true-failure case initially passed only because the old
unsafe direct unlink still ran; the regression was strengthened by making that
direct unlink fail, proving that durable recovery was required.

### GREEN and verification

The focused new regression run passed: `6 passed, 1 warning in 1.53s` (the two
permanent-delete cases are parameterized).

Final focused inventory command:

```text
.venv/bin/pytest -q \
  tests/test_inventory_catalog.py \
  tests/test_inventory_counts.py \
  tests/test_models_migration.py
```

Result: `137 passed, 1 warning in 29.94s`. The warning is the existing
Starlette `httpx` deprecation warning.

Final Ruff command:

```text
.venv/bin/ruff check --no-cache \
  src/robopark_api/services/inventory_catalog.py \
  src/robopark_api/services/inventory_deletion.py \
  src/robopark_api/services/inventory_photo_cleanup.py \
  tests/test_inventory_catalog.py
```

Result: `All checks passed!`. `git diff --check` also passed.

### Commit ambiguity, locking and compatibility reasoning

- A raised commit is treated as ambiguous. The replacement blob is never
  directly unlinked from that exception path. A fresh session records a
  durable cleanup intent, then the cleanup processor rechecks live component,
  part and robot references before unlinking.
- If the database commit succeeded but its acknowledgement was lost, the fresh
  session sees the new reference, keeps the blob and completes the cleanup
  marker. If the commit truly failed, the same processor sees no reference and
  removes the orphan. This preserves recovery across process boundaries and
  makes retry behavior reference-safe.
- Catalog photo replace/remove selects the catalog row `FOR UPDATE` before
  reading its predecessor key. Permanent component/part deletion locks every
  affected catalog row before snapshotting photo keys or bulk deleting rows,
  so a concurrent replacement cannot create an unqueued predecessor.
- SQLite uses a no-op row update to obtain an early writer reservation because
  it ignores `FOR UPDATE`; other supported dialects use the explicit row lock.
- No new migration is needed for the follow-up: it reuses the additive
  `inventory_photo_cleanup` table introduced in migration `0038`.

### Follow-up changed files

- `apps/api/src/robopark_api/services/inventory_catalog.py`
- `apps/api/src/robopark_api/services/inventory_deletion.py`
- `apps/api/src/robopark_api/services/inventory_photo_cleanup.py`
- `apps/api/tests/test_inventory_catalog.py`
- `.superpowers/sdd/2026-09-23-task-workflows-media-inventory/final-fix-a-report.md`
