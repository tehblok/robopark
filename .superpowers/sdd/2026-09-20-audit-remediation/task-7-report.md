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

## Review remediation round 3

- PostgreSQL locking now constructs a cached, lock-only `NullPool` Engine from
  the bound URL. Advisory lock acquisition uses that independent connection,
  never the potentially saturated request `QueuePool`; both connection setup
  and `pg_try_advisory_xact_lock` share the five-second deadline. The cached
  lock engines are disposed during lifespan shutdown before the job lease is
  released.
- SQLite no longer serializes every key through one lock file. It maps each
  key deterministically to one of 64 stable sidecars, while retaining the
  ref-count-pruned in-process keyed lock. File count is bounded at 64, and
  different buckets can progress concurrently across host processes.
- Added a request-pool isolation fake with a prechecked size-one `QueuePool`,
  child-process `flock` exclusion/concurrency coverage (skipped only on
  platforms without `fcntl`), and a bounded-file/process-lock-registry check.
- Lifecycle coverage now verifies cached lock engines are disposed after every
  background writer and Push service have stopped, before lease release.

Round-3 short verification:

- `tests/test_database_locks.py tests/test_offline_sync.py
  tests/test_media_uploads.py tests/test_lifespan_jobs.py` — `18 passed`
  (only the existing Starlette/httpx deprecation warning).
- Scoped Ruff/format, `sh -n scripts/verify.sh` and `git diff --check` passed.
- No PostgreSQL container, Docker, full suite, load, soak, installer or OTA
  operation was run.

## Review remediation round 4

- Replaced the unbounded lock-only `NullPool` with a fully separate two-slot
  `QueuePool` (`max_overflow=0`) plus a matching per-worker bounded semaphore.
  With two API workers this caps lock-only PostgreSQL connections at four,
  independently of the request pool and well below the supported 40-connection
  deployment budget.
- Semaphore admission, lock-pool checkout, physical connection setup and
  advisory polling now consume one five-second wall-clock deadline. The pool
  timeout is explicitly five seconds (not SQLAlchemy's 30-second default), and
  physical psycopg connections receive only the remaining deadline.
- Cache shutdown now disposes the wrapped bounded engines after the existing
  lifecycle barrier has stopped writers.
- Added a deterministic three-contender unit contract: two physical lock
  connections may be active, the third times out without opening another, and
  an independent request-pool query remains usable.
- Hardened the SQLite child-process contract. The contender announces its
  attempt before entering the lock and separately signals an observed
  `BlockingIOError`, proving it actually reached the held `flock`; release and
  join coordination use ARM-safe explicit event waits rather than a 150 ms
  timing assertion.

Round-4 TDD / short verification:

- RED: `tests/test_database_locks.py` failed three PostgreSQL tests because the
  bounded `_PostgresLockEngine` contract did not exist; the pre-existing
  `NullPool` implementation could not satisfy the cap.
- GREEN: `tests/test_database_locks.py tests/test_lifespan_jobs.py` — `6 passed`
  (only the existing Starlette/httpx deprecation warning).
- Scoped Ruff, format check and `git diff --check` passed.
- No PostgreSQL container, Docker, full suite, load, soak, installer, OTA or
  signature operation was run.

## Final deadline remediation

- Removed `pool_pre_ping` from the dedicated lock pool so checkout cannot add
  an unbudgeted network round trip. Lock connections are recycled on every
  checkout while the two-slot pool and semaphore continue to bound physical
  connection concurrency.
- Physical PostgreSQL connect settings now conservatively floor the remaining
  deadline instead of rounding upward. A connect is rejected before reaching
  the driver when less than its one-second granularity remains. PostgreSQL
  also receives remaining-budget `statement_timeout`, `lock_timeout`, and
  `tcp_user_timeout` values.
- Added monotonic checks immediately after checkout and after every advisory
  scalar response. A result that arrives after the five-second deadline is
  rolled back/closed and reported as `idempotency_lock_busy`, even when the
  database returned `true`.
- Added deterministic 50 ms deadline regressions for a 200 ms fake connect and
  a 200 ms fake advisory scalar. Neither is allowed to enter the protected
  workflow.

Final short verification:

- RED: both late-success cases entered the protected workflow and failed with
  `DID NOT RAISE HTTPException` before the fix.
- GREEN: `tests/test_database_locks.py tests/test_lifespan_jobs.py` — `8 passed`
  (only the existing Starlette/httpx deprecation warning).
- Scoped Ruff, format check and `git diff --check` passed.
- No PostgreSQL container, Docker, full suite, load, soak, installer, OTA or
  signature operation was run.

## Final phase-budget remediation

- Split the remaining PostgreSQL lock deadline into non-overlapping driver
  phases: 40% for connect, 40% for the advisory query, and 20% safety/cleanup.
  At the normal five-second deadline this is two seconds + two seconds + one
  second; flooring for libpq's whole-second connect timeout can only shorten
  the combined budget.
- The physical connection startup options now carry the query-phase
  `statement_timeout` and `lock_timeout` before the first SQL statement;
  `connect_timeout` is limited to the separate connect phase and
  `tcp_user_timeout` to the query phase. Post-connect and post-query monotonic
  checks remain in place.
- SQLAlchemy `DBAPIError`/driver timeout failures from connect, transaction
  setup, or advisory acquisition are mapped to retryable
  `503 idempotency_lock_busy`. The catch scope ends before `yield`, so database
  or programmer exceptions raised by the protected workflow retain their
  original type and identity.
- Added cleanup assertions proving transaction/connection exit and semaphore
  release after a driver timeout. A phase-aware fake consumes its configured
  one-second query budget and confirms total elapsed time remains below the
  2.5-second total contract. Another assertion proves a protected-body
  `OperationalError` is not remapped.

Final phase-budget TDD / short verification:

- RED: the phase allocator did not exist and a fake driver timeout escaped as
  raw `OperationalError`.
- GREEN: `tests/test_database_locks.py tests/test_lifespan_jobs.py` — `11 passed`
  (only the existing Starlette/httpx deprecation warning).
- Scoped Ruff, format check and `git diff --check` passed.
- No PostgreSQL container, Docker, full suite, load, soak, installer, OTA or
  signature operation was run.

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
