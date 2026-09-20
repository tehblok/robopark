# Task 4 report: bounded storage, leak observation and acceleration probes

## Result

- Added one storage-pressure contract with a free-space floor of
  `max(15% of the partition, 6 GiB)`, deterministic category order, TTL/log caps,
  dry-run output and a 128-item execution batch.
- Scheduled host cleanup owns only the real host diagnostics and Robopark log roots.
  API cleanup reuses the existing live-merge, report-attachment and delivery-confirmed
  Tracker-upload owners. It does not scan releases,
  rollback/recovery, PostgreSQL, inventory, users/roles/parks, diagnostic rules,
  active work, pending actions or unconfirmed uploads.
- Cleanup opens every root component without following symlinks, pins directory
  descriptors, and revalidates before descriptor-relative unlink. It refuses symlink
  entries, hard-linked files and changed inodes. It executes under Task 2's stable host lock and records a
  bounded category report for doctor and host health.
- Existing API outbox retention remains delivery-gated: only `succeeded` actions and
  uploaded staged blobs are eligible. Existing report deletion markers remain the
  crash-safe owner for report attachment cleanup.
- Host health now reports RSS and its trend, file descriptors, tasks/threads, Task 3
  cache bytes, checked-out DB connections, allowlisted directory sizes, storage floor,
  last cleanup and hardware capabilities.
- Three consecutive cgroup samples above 90% evict Task 3 Tracker/Emergency caches
  once. Continued pressure or an eviction error is surfaced; no restart is requested.
- Added fail-soft VIM4, New VIM4, Orin and generic ARM capability probes. Because no
  hardware thumbnail consumer exists yet, JPEG remains software and hardware JPEG is
  not advertised; plugin/library presence alone is not treated as a health probe.
  CUDA/NPU discovery is informational and never gates startup.
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

## Review round 1

- Host and API bounded cleanup now retain at most 128 candidates in memory, aggregate
  skipped reasons, and keep reports bounded under thousand-entry stress fixtures.
- Parent replacement during cleanup cannot redirect deletion: regression tests swap
  the visible root immediately before unlink and verify the outside file is untouched.
- Cleanup failures use exponential 30–900 second retry, while memory-pressure sampling
  continues every interval; identical cleanup errors are logged at most once per cap.
- The API computes disk health from its real `/data` mount and reads capabilities and
  last cleanup only from the sanitized `/ops/host-health.json` projection. Runtime and
  manual Compose contracts name both paths and never mount private host state.
- Health directory observations now use the real Task 3 live-merge store, report
  attachments and staged Tracker uploads. Tracker blob deletion remains gated by an
  uploaded attachment joined to a succeeded reliable action.
- Verification: focused matrix `69 passed`; full host `752 passed in 486.56s`; full API
  `1789 passed, 7 skipped`; full web `147 files / 2080 tests`; scoped Ruff clean;
  web lint exited 0 (existing warnings only) and production build succeeded.

## Review round 2

- Candidate eligibility is computed per category before bounded selection. Category
  size/cap pressure outranks TTL cleanup, followed by configured category priority and
  age, while memory remains proportional to the 128-item batch. The one-slot regression
  with an expired diagnostic and a fresh 300 MiB log now selects the over-cap log.
- Scheduled API cleanup now measures the actual `/data` filesystem with `StorageBudget`
  and exhausts bounded batches in owner order: live-merge cache/tmp, deleted-report
  diagnostic/log quarantine, then staged Tracker copies whose upload is joined to a
  succeeded reliable action. Host cleanup retains exclusive ownership of host logs and
  diagnostics, so the two schedulers do not delete the same paths.
- Confirmed local Tracker copies and their duplicate attachment rows may be retired
  under disk pressure; reliable-action audit remains. Unconfirmed files, actions and
  primary application data remain untouched. The fixed-size aggregate result is written
  to `/ops/api-storage-retention.json` and exposed in admin host health.
- Verification: focused host `11 passed`; focused API owners/health/live-merge/reports
  `72 passed`; full host `753 passed in 480.63s`; full API `1790 passed, 7 skipped`;
  full web `147 files / 2080 tests`; scoped Ruff and diff checks passed; web lint exited
  0 with existing warnings and production build succeeded.

## Review round 3

- Live-merge pressure pruning now streams namespace and file entries through pinned
  no-follow descriptors and descriptor-relative revalidation/unlink. Symlinked roots,
  namespaces and entries are rejected; a parent-swap regression proves outside files
  cannot be selected.
- Atomic writers hold an advisory lock on their temporary inode through replacement.
  Pressure cleanup never lowers the conservative one-hour abandoned-temp threshold and
  must also acquire that inode lock before unlinking. A concurrent real `_atomic_write`
  remains intact even when the cleanup clock is advanced beyond the age threshold.
- Results, errors, inflight markers, temporary files and lock files all share the same
  deletion ceiling. Scans do not materialize directory contents and receive the global
  deadline. The coordinator yields after 512 deletions, 16 owner iterations or 0.5 s,
  and reports deterministic `partial` and `stop_reason` fields.
- Manual Compose, installed runtime and candidate smoke Compose explicitly set
  `LIVE_MERGE_DIR=/data/live-merge` and
  `STAGED_ATTACHMENTS_DIR=/data/task-attachments`. Existing SQLite-derived defaults
  remain compatible, while production owners are guaranteed to share the `/data`
  filesystem measured by `StorageBudget`.
- Verification: focused host `34 passed`; focused API `46 passed`; full host
  `753 passed in 479.79s`; full API `1796 passed, 7 skipped`; full web
  `147 files / 2080 tests`; scoped Ruff and diff checks passed; web lint exited 0 with
  existing warnings and production build succeeded.

## Review round 4

- Live-merge and staged Tracker root factories now preserve the configured lexical
  path. They no longer resolve a root or ancestor symlink before the pinned
  `O_NOFOLLOW` walk, so a `/data`-shaped alias to protected storage is rejected and
  no outside file or confirmed-upload metadata is retired.
- The pressure coordinator propagates its absolute 0.5-second deadline, remaining
  deletion allowance and a 4096-entry scan allowance through confirmed Tracker
  cleanup into the streaming storage scanner. Reports include `scanned_count`,
  `partial` and the exact `stop_reason` for deadline/scan/deletion exhaustion.
- A partial or failed scan never classifies unseen eligible blobs as missing. Only a
  completed scan may retire metadata for an already-absent, delivery-confirmed local
  copy; unconfirmed uploads and reliable-action audit rows remain protected.
- RED evidence: the live-merge ancestor-symlink regression deleted one outside file;
  the staged-root regression deleted the outside upload and its row; the protected
  upload stream exceeded the sentinel instead of observing the coordinator deadline.
- Verification: focused API retention/live-merge/health/report/outbox matrix
  `113 passed`; full host `753 passed in 491.59s`; full API `1800 passed, 7 skipped`;
  full web `147 files / 2080 tests`; production build, scoped Ruff `E,F,I` and diff
  checks passed.
