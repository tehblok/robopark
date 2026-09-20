# Task 7 report — short PostgreSQL and lifecycle integration contracts

Status: complete

## Implemented

- Added three PostgreSQL-at-migration-head HTTP contracts:
  - replaying the same offline sync batch returns its durable receipt without
    dispatching the workflow twice;
  - resumable media completion is idempotent;
  - a current shift plus Push subscription persists and creates the expected
    internal task notification for the same user.
- Retained the existing PostgreSQL inventory decrement race and Task 4 shared
  throttle concurrency contracts in the same module.
- Added a deterministic lifecycle test with faked workers. It verifies the
  host maintenance barrier prevents lease acquisition and writers, then checks
  shutdown stops workers before the job lease is released. It uses event waits
  only (maximum two seconds), not a real background loop or sleep.
- Split `scripts/verify.sh` into explicit `fast`, `full`, `load` and `soak`
  targets. `fast` excludes Docker, PostgreSQL, image builds, browser install,
  capacity and soak. `load` and `soak` require explicit invocation; soak also
  requires duration and output path. `all` remains a compatibility alias for
  `full`.
- Documented the target purpose and expected duration in the acceptance matrix.

## TDD / verification evidence

RED:

- `./scripts/verify.sh fast` initially exited with usage because no `fast`
  target existed.

GREEN / short checks:

- `apps/api/.venv/bin/python -m pytest -p no:cacheprovider -q apps/api/tests/test_lifespan_jobs.py`
  — `1 passed` (Starlette/httpx deprecation warning only).
- `apps/api/.venv/bin/python -m pytest -p no:cacheprovider -q apps/api/tests/postgres/test_schema_and_workflows.py -k 'http_sync or http_media or http_schedule'`
  — `3 skipped, 9 deselected`: PostgreSQL execution is deliberately opt-in
  through `ROBOPARK_POSTGRES_TESTS=1`; no container was started.
- `apps/api/.venv/bin/ruff check apps/api/tests/postgres/test_schema_and_workflows.py apps/api/tests/test_lifespan_jobs.py`
  — passed.
- `apps/api/.venv/bin/ruff format --check ...` — passed.
- `sh -n scripts/verify.sh` and `git diff --check` — passed.

The first attempted `./scripts/verify.sh fast` after implementation could not
initialize the sandboxed shared uv cache (`~/.cache/uv` permission denied), so
the targeted lifecycle test used the already-provisioned project virtualenv.
That is an environment limitation, not a test failure.

## Deliberately not run

- PostgreSQL contracts were not executed because the fixture would create a
  disposable Docker container, and this task forbids Docker unless an existing
  service is already available.
- No full API/web suite, Docker build, load benchmark, soak, installer/VM or
  OTA/signature operation was run or changed.

## Review remediation

- Added real `load` and `soak` command branches. They are unreachable from
  `fast` and `full`; `soak` was checked fail-safe without its required
  environment and stopped before any browser/container work.
- Replaced the sequential PostgreSQL sync replay contract with two concurrent
  HTTP requests held across the dispatch path. PostgreSQL now uses a
  per-receipt session advisory lock, so the workflow dispatch and receipt
  persist exactly once even though lower layers may commit independently.
- Replaced the sequential media completion assertion with two concurrent HTTP
  completions held at the staged-file rename. Completion now reads the upload
  row with `FOR UPDATE`, ensuring the second caller observes the completed
  result rather than a missing staged file.
- The schedule/push contract now opens a fresh database session and verifies
  the persisted encrypted subscription row belongs to the HTTP user.
- The lifecycle test now tracks all eight individual jobs and a fake
  `PushService.close`; lease release asserts every job and Push service has
  stopped, rather than relying on one shared event.

Additional short verification:

- `tests/test_offline_sync.py tests/test_media_uploads.py tests/test_lifespan_jobs.py`
  — `12 passed` (only the existing Starlette/httpx deprecation warning).
- New PostgreSQL selections remain `3 skipped` because the opt-in Docker gate
  is absent; Docker was not started.
- Scoped Ruff/format, `sh -n scripts/verify.sh` and `git diff --check` passed.

## Review remediation round 2

- Replaced the request-session PostgreSQL advisory lock with a bounded
  transaction-scoped `pg_try_advisory_xact_lock` held on a dedicated Engine
  connection. This connection remains alive even if a request `Session` drops
  and rebinds while a Tracker dependency yields; the transaction context
  releases the lock deterministically and surfaces close/transaction errors.
- Added `database_locks.py`, shared by offline receipts and media completion.
  SQLite uses a ref-counted, prunable process keyed lock plus a single stable
  nonblocking `flock` sidecar file for host-worker coordination. The single
  sidecar prevents unbounded lock-file accumulation on constrained devices.
- Media completion now uses that lock around the row read, staged-file rename
  and commit. The existing PostgreSQL row lock remains an additional database
  safeguard; the SQLite path now has equivalent cross-session serialization.
- Added real separate-session/thread SQLite regressions for duplicate sync
  dispatch/receipt and concurrent media completion. A focused fake test also
  proves the PostgreSQL lock is on its dedicated transaction while the request
  session rebinds.

Round-2 short verification:

- `tests/test_database_locks.py tests/test_offline_sync.py
  tests/test_media_uploads.py tests/test_lifespan_jobs.py` — `15 passed`
  (only the existing Starlette/httpx deprecation warning).
- PostgreSQL concurrent-contract selection — `3 skipped` under the absent
  opt-in gate; Docker was not started.
- Scoped Ruff/format, `sh -n scripts/verify.sh` and `git diff --check` passed.
