# Task 4 report: shared authentication throttle

## Outcome

- Replaced per-process login/register counters with `AuthThrottleState` rows shared by all API workers.
- Failure increments use a dialect-specific `INSERT ... ON CONFLICT DO NOTHING` followed by one atomic conditional `UPDATE`.
- Only SHA-256 digests are persisted; raw usernames and addresses are never stored in throttle state.
- Successful login/registration removes its throttle state.
- Registration retains the per-address shared-password gate in addition to the required username/address key, so rotating usernames cannot bypass it.
- Added bounded expiry cleanup (maximum 500 rows per normal cleanup cycle).
- Cleanup rechecks `expires_at <= cutoff` in the final conditional `DELETE`, so a row renewed after the bounded selection cannot be removed from stale state.
- Added a PostgreSQL-gated eight-worker concurrency regression for the atomic increment/lock transition.

## TDD evidence

- RED: DB-backed constructor/cross-instance test failed because `session_factory` was not supported.
- RED: bounded cleanup test failed because `prune_auth_throttle_states` did not exist.
- RED: rotating registration usernames bypassed the shared-password throttle and returned 201 instead of 429.
- RED (review fix): the stale-selection cleanup regression failed because the conditional delete helper did not exist.
- GREEN: all three behaviors now pass through real SQLite-backed sessions and HTTP routes.

## Verification

- `apps/api/.venv/bin/pytest -q tests/test_login_throttle.py tests/test_register.py tests/test_security_hardening.py tests/test_cache_cleanup.py -x`
  - `57 passed`, one pre-existing Starlette/httpx deprecation warning.
- `apps/api/.venv/bin/pytest -q tests/postgres/test_schema_and_workflows.py::test_auth_throttle_concurrent_failures_lock_once_across_postgresql_workers`
  - correctly gated: `1 skipped` because `ROBOPARK_POSTGRES_TESTS=1` was not supplied; Docker was not started.
- `apps/api/.venv/bin/ruff check src/robopark_api/services/login_throttle.py src/robopark_api/routers/auth.py src/robopark_api/services/cache_cleanup.py tests/test_login_throttle.py tests/test_cache_cleanup.py tests/postgres/test_schema_and_workflows.py`
  - passed.
- `git diff --check`
  - passed.

PostgreSQL concurrency execution was not run because the task explicitly forbids starting Docker/long infrastructure tests. Its test is part of the existing opt-in PostgreSQL gate and is ready for a later permitted run.
