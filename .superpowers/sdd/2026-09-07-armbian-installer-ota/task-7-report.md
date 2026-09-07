# Task 7 report — Royal host bridge and sanitized health API

## Delivered

- Added Royal-only `GET /admin/ops/system-health`, `POST /admin/ops/diagnostics`, `POST /admin/ops/repair`, `GET /admin/ops/diagnostic-artifact`, `POST /admin/ops/update/inspect`, and `POST /admin/ops/update/approve`. Admin/operator return 403 outside host maintenance; the authoritative host barrier returns 503 for every mutating request before role exemptions are considered.
- Kept ordinary snapshot/restore state in the API ops directory. Installed `OPS_HOST_ROOT` opts `build_ops_context` into host updating, with a distinct validated exchange root. Missing roots, symlinked exchange directories, overlapping API/host roots, and exposed private host state fail closed. The installed runtime already supplies `/ops`, `/host-ops`, the two RW exchange binds, the RO public bind and mounted public key.
- Inspection checks format-2 Ed25519 signature and member hashes, ignores client filenames, stores a generated `update-<inspection UUID>.zip`, and persists its hash and Royal actor in private API state.
- Approval requires the exact phrase `ОБНОВИТЬ`, binds the inspection to its original actor, checks the saved archive again, and persists one request of exactly `{job_id, kind, artifact, actor_user_id, created_at}`. The existing Task 6 job ownership guard prevents abort/expiry after durable dispatch. Publication uses a fsynced prepared file and no-clobber hard link into the single approved slot. Retrying the inspection reuses the exact saved request; a host claim suppresses republication. Consumed inspections cannot dispatch again after later jobs.
- Added a root command consumer and `robopark-commands.path` / oneshot service. Both default updater boot dispatch and the path trigger use the consumer. A separate outer consumer lock prevents simultaneous launchers; claim/removal and generic work use the existing `host.lock`. Updates are forwarded to the Task 6 launcher/worker/recovery flow, while diagnostics/repair use Task 5 routines. Root receipts prevent replay; interrupted repairs fail without executing again. The consumer service retries crashes with bounded backoff. Expired but structurally valid approvals publish a controlled failure; an expired root-claimed update first runs Task 6 recovery, and never receives a refreshed approval timestamp.
- Extended installer and OTA/rollback unit allowlists, enablement and restart handling for the new trigger. Installer behavior is tested with captured systemctl calls.
- Diagnostics creates root-owned mode-0644 ZIPs in `ops/public/artifacts/<job_id>.zip` and an allowlisted public result. API downloads resolve only the exact completed diagnostic job artifact and reject symlinks. Repair publishes before/after checks and fixed repair IDs. `GET /admin/ops/job` adds nullable `host_result` containing only `before`, `after`, `performed`, and `failed`.
- Scheduled doctor now publishes mode-0644 public health, including validated backup status/time. API health has exactly `version`, `git_sha`, `generated_at`, `overall`, `checks`, `update`, `last_backup`; messages are generated from a local check dictionary. Arbitrary nested JSON, host error text, paths and command fields never pass through the projection.
- Hardened the existing ZIP metadata projection: Compose values are finite typed operational fields; journal timestamps/priorities/event IDs and release metadata are validated. No journal messages, argv, environment or cookies are exported.
- API audit records include Royal actor and inspection/approval/diagnostics/repair/download acceptance or rejection. Durable root command receipts retain the actor, exact request and completion outcome.
- API startup and safe polling reconcile sanitized host results; maintenance middleware reads the root marker from the RO public bind. Existing manual snapshot/restore behavior remains covered by regression tests.

## HTTP contract for subsequent UI work

- Inspect: multipart field `archive`; response `{inspection_id, version, git_sha, migration_head, notes}`.
- Approve: JSON `{inspection_id, confirm: "ОБНОВИТЬ"}`; response `OpsJobOut`.
- Diagnostics/repair: POST with no command arguments; response `OpsJobOut`, poll `GET /admin/ops/job`.
- Diagnostic download: no artifact/path parameter; downloads the current completed diagnostic job.
- `host_result` is nullable; when present it has sanitized `before`/`after` check arrays and allowlisted `performed`/`failed` repair ID arrays.
- 409 means conflicting host/API work or a previously consumed inspection; 503 means unavailable/misconfigured installed bridge; 400 is a rejected inspection/approval.

## TDD and verification

Initial RED: 17 API failures (missing routes/context) and 8 host failures (missing consumer/trigger). Subsequent RED/GREEN cycles covered publication crash retry, actor/artifact binding, read-only host maintenance, symlink downloads, raw/nested host JSON, inspection replay after another job, root repair crash recovery, oversized receipt replay, unsafe journal/Compose metadata, safe repair result, backup projection, and expired-approval recovery.

Final checks from this worktree:

- API ops/security/abort/runner/snapshot/archive/signature selection: **97 passed**.
- `tests/host`: **220 passed**.
- `sh tests/host/test_installer.sh`: **36 passed**; real installer entrypoint with temporary host and fake APT/systemd/Docker/Tuna adapters.
- Ruff check on all 17 changed/new Python implementation/test files: **passed**.
- Ruff format check on the same 17 files: **passed**.
- `sh -n deploy/installer/lib/services.sh`: **passed**.
- `git diff --check`: **passed**.

The API run emits the existing Starlette/httpx deprecation warning. It is unrelated to these changes.

Commands used the existing shared interpreter and Ruff executables from:
`/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/armbian-installer-ota/apps/api/.venv/bin/`
with the **Task 7 working directory** and `PYTHONPATH=apps/api/src:deploy/host` (host-only runs: `PYTHONPATH=deploy/host`). No source files were changed in another worktree. A fresh uv environment was not usable under restricted networking; the shared test runtime was used instead.

API selection:
`apps/api/tests/test_ops_host_bridge.py apps/api/tests/test_ops_http.py apps/api/tests/test_security_hardening.py apps/api/tests/test_ops_jobs_abort.py apps/api/tests/test_ops_runner.py apps/api/tests/test_ops_snapshot.py apps/api/tests/test_ops_archives.py apps/api/tests/test_release_signing.py`

## Verification limits

The checks ran on macOS with temporary host roots and fake privileged adapters. Actual Linux systemd path activation, production bind-mount permissions, Docker image execution and Armbian device acceptance belong to the later deployment/E2E gate; no production host was changed. A diagnostic command reports successful collection even when its health checks contain failures; repair reports failed repair actions separately from any remaining unrepairable health checks.


## Review fix round 1

Closed the critical writer-barrier finding and all three important findings from `task-7-review.md`:

- Host maintenance is authoritative before the legacy initiating-session/Royal exemption. All mutating methods, including Royal repair, abort, inspection and approval, return `503 maintenance`. Only an explicit GET/HEAD health, status and artifact allowlist plus OPTIONS remains available. Status polling does not slide authentication-session expiry. The application DB write/commit boundary rejects writes from handlers which entered before the marker appeared; a rejected commit explicitly rolls back the underlying DBAPI transaction so pooled connection reuse cannot later commit it.
- Candidate startup postpones application seeds, secret migrations, worker leases and background workers until the root marker is released. Readiness remains usable during this wait. Session cleanup, blocker scans and Emergency keepalive recheck the same gate at their concrete cycles and after upstream calls. LiveMerge result publication, report-attachment publication and delayed restore also recheck before writing/replacing data. Read-only candidate DB connections retain connection-local foreign-key/timeout settings without changing persistent journal mode. Task 6 host-update publication remains an operational request delegated to the root writer barrier.
- The dedicated marker reader treats malformed/oversized/deep JSON, duplicate fields, invalid schema/types, symlinks, unreadable state and missing/invalid public mounts as maintenance. Only a definitely absent marker under a valid public directory or a valid `enabled:false` marker allows writes. Middleware, public status, host-idle checks and terminal reconciliation use it consistently.
- Generic diagnostics/repair requests retain the exact persisted job, actor and timestamp across retry. If publication fails and both command slot and claim are definitely absent, the job returns to `pending`; if publication/claim is uncertain, ownership remains dispatched. Same-actor retries and polling reconciliation republish the saved request, while a matching root claim suppresses a second execution.
- The command service uses `StartLimitIntervalSec=0`, preserving serialization, timeout and restart backoff. A durable per-command attempt counter caps crashed command attempts at three independently of successful command throughput; an exhausted update remains in maintenance for manual recovery. Four consecutive successful commands and repeated process-crash attempts are tested separately. The root JSON reader also normalizes `RecursionError`.

TDD evidence: the first 18 new API regressions failed before the fixes; they cover Royal mutation, invalid/unreadable markers, actual cleanup/blocker/keepalive writes and exact generic retry/crash recovery. Further RED cases covered rejected-commit transaction reuse, LiveMerge/attachment file publication, delayed restore at both start and replacement, candidate seed/worker resumption, and connection-local foreign-key protection. Host RED cases covered the service start limit, per-command crash budget and recursive JSON normalization. Final publication-uncertainty checks also verify that a root claim prevents replay.

Round-1 verification uses the same shared Python 3.13 and Ruff runtime noted above. The full API suite must run from this worktree's `apps/api` directory because existing Alembic configuration resolves `alembic` relative to cwd (`PYTHONPATH=src:../../deploy/host`). Host runs use the worktree root and `PYTHONPATH=deploy/host`. Installer runs prepend the shared runtime's `bin` to PATH so the shell harness and all fake-command subprocesses use the same Python.

Initial broader verification exposed invocation mistakes: running the entire API suite from the repository root failed twelve cwd-dependent migration tests; the default installer shell selected the system Python 3.9 and that run was interrupted. Both invocations were corrected. The API run also exposed an overly broad new guard on the old Task 6 operational runner; this was narrowed to local data tasks and all 23 existing runner tests passed. No environment sync or network installation was performed.

Final round-1 results:

- Focused bridge/review/HTTP/runner API selection: **83 passed** (including **27 new review regressions**).
- Full API suite, cwd `apps/api`: **760 passed**, one existing Starlette/httpx deprecation warning.
- Full `tests/host`: **223 passed**.
- `PATH=<shared-runtime>/bin:$PATH sh tests/host/test_installer.sh`: **36 passed**.
- Ruff check and format check using `--config apps/api/pyproject.toml` on all **18 changed/new Python files**: **passed**.
- Shell syntax and `git diff --check`: **passed**.

Linux acceptance remains a deployment gate: on a disposable installed Linux host, submit four distinct valid diagnostics commands sequentially inside five minutes and verify four results and an active path unit; separately inject repeated launcher process failure for one job and verify that the persisted three-attempt budget leaves maintenance active without another automatic launch. This macOS run proves unit configuration and consumer behavior with fake adapters, not actual systemd activation or Docker/Armbian rollback. Retention policy for inspection uploads, root receipts and public artifacts remains a later operations-integration item from the review.
