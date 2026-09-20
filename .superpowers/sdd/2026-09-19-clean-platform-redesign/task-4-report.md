# Task 4 report: bounded storage, leak observation and acceleration probes

## Result

- Added one storage-pressure contract with a free-space floor of
  `max(15% of the partition, 6 GiB)`, deterministic category order, TTL/log caps,
  dry-run output and a 128-item execution batch.
- Scheduled host cleanup owns only explicit ephemeral roots: cache, thumbnails,
  diagnostics, Robopark logs and confirmed Tracker copies. It does not scan releases,
  rollback/recovery, PostgreSQL, inventory, users/roles/parks, diagnostic rules,
  active work, pending actions or unconfirmed uploads.
- Cleanup refuses symlink roots, symlinked ancestors, symlink entries, hard-linked
  files and changed inodes. It executes under Task 2's stable host lock and records a
  bounded category report for doctor and host health.
- Existing API outbox retention remains delivery-gated: only `succeeded` actions and
  uploaded staged blobs are eligible. Existing report deletion markers remain the
  crash-safe owner for report attachment cleanup.
- Host health now reports RSS and its trend, file descriptors, tasks/threads, Task 3
  cache bytes, checked-out DB connections, allowlisted directory sizes, storage floor,
  last cleanup and hardware capabilities.
- Three consecutive cgroup samples above 90% evict Task 3 Tracker/Emergency caches
  once. Continued pressure or an eviction error is surfaced; no restart is requested.
- Added fail-soft VIM4, New VIM4, Orin and generic ARM capability probes. JPEG hardware
  is selected only after a bounded local plugin/library probe; software remains the
  fallback. CUDA/NPU discovery is informational and never gates startup.
- Admin UI exposes the selected profile/JPEG backend, storage budget, cleanup state,
  process/leak signals and persistent memory-pressure failure. The database label now
  reflects PostgreSQL.

## Changed files

- Host policy and probes: `deploy/host/robopark_host/{retention,image_retention,capabilities,doctor,runtime,cli}.py`.
- API retention and health: `apps/api/src/robopark_api/services/{storage_retention,cache_cleanup,operational_health}.py`,
  `apps/api/src/robopark_api/routers/admin_health.py`.
- Admin health UI/schema: `apps/web/src/components/admin/{hostHealthApi,HostHealthPanel}.tsx|ts`.
- Tests: `tests/host/test_task4_storage_capabilities.py`,
  `tests/host/test_{retention,image_retention,doctor,runtime}.py`,
  `apps/api/tests/test_task4_storage_health.py`, API retention/health/outbox/report suites,
  and `apps/web/src/components/admin/HostHealthPanel.test.tsx`.

## RED evidence

- Initial focused run: 7 failures for missing `StorageBudget`, unified cleanup,
  `HostCapabilities`, leak observations and pressure controller.
- Builder/doctor/pressure run: 3 failures for the former 2 GiB builder threshold,
  missing bounded-category doctor output and missing cache-first pressure handling.
- Symlinked-parent test deleted through an aliased root before ancestor validation.
- Eviction-error test raised the cache backend exception instead of reporting failure.
- UI test could not find the persistent-pressure alert before it was implemented.

## GREEN and verification evidence

- Full host: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/host:apps/api/src apps/api/.venv/bin/python -m pytest -p no:cacheprovider -q tests/host`
  -> `749 passed in 469.23s`.
- Final focused host retention/doctor/runtime matrix -> `56 passed in 5.49s`.
- Final API cleanup/health/report/outbox/host-bridge matrix, run from `apps/api`,
  -> `140 passed, 1 pre-existing Starlette warning in 37.17s`.
- Full web: `npm test` -> `147 files, 2080 tests passed`.
- `npm run lint -- --quiet` exited 0; `npm run build` completed successfully.
- Scoped Ruff `E,F,I` checks passed; `git diff --check` passed.

## Self-review and residual risk

- No Task 2 PostgreSQL, restore, updater or lock lifecycle was replaced. The full host
  suite is the regression evidence.
- No Task 3 cache owner or metric registry was duplicated; pressure eviction calls the
  existing cache owners and observations consume the existing registry.
- Cleanup is intentionally non-recursive. Nested cache formats require their owner to
  expose a dedicated allowlisted root rather than broadening traversal.
- Hardware fixtures prove selection and fallback logic. Physical VIM4/Orin encode
  throughput and the eight-hour soak remain Task 8 acceptance evidence.
- The API matrix was first invoked from the repository root and produced two Alembic
  path failures in report tests; rerunning from the suite's required `apps/api` cwd
  passed all 140 tests. This was a command-context issue, not a product failure.
