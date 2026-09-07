# Task 7 report — Royal host bridge and sanitized health API

## Delivered

- Added Royal-only `GET /admin/ops/system-health`, `POST /admin/ops/diagnostics`, `POST /admin/ops/repair`, `GET /admin/ops/diagnostic-artifact`, `POST /admin/ops/update/inspect`, and `POST /admin/ops/update/approve`. Admin/operator return 403, including while maintenance is active.
- Kept ordinary snapshot/restore state in the API ops directory. Installed `OPS_HOST_ROOT` opts `build_ops_context` into host updating, with a distinct validated exchange root. Missing roots, symlinked exchange directories, overlapping API/host roots, and exposed private host state fail closed. The installed runtime already supplies `/ops`, `/host-ops`, the two RW exchange binds, the RO public bind and mounted public key.
- Inspection checks format-2 Ed25519 signature and member hashes, ignores client filenames, stores a generated `update-<inspection UUID>.zip`, and persists its hash and Royal actor in private API state.
- Approval requires the exact phrase `ОБНОВИТЬ`, binds the inspection to its original actor, checks the saved archive again, and persists one request of exactly `{job_id, kind, artifact, actor_user_id, created_at}`. The existing Task 6 job ownership guard prevents abort/expiry after durable dispatch. Publication uses a fsynced prepared file and no-clobber hard link into the single approved slot. Retrying the inspection reuses the exact saved request; a host claim suppresses republication. Consumed inspections cannot dispatch again after later jobs.
- Added a root command consumer and `robopark-commands.path` / oneshot service. Both default updater boot dispatch and the path trigger use the consumer. A separate outer consumer lock prevents simultaneous launchers; claim/removal and generic work use the existing `host.lock`. Updates are forwarded to the Task 6 launcher/worker/recovery flow, while diagnostics/repair use Task 5 routines. Root receipts prevent replay; interrupted repairs fail without executing again. The consumer service retries crashes with bounded backoff. Expired but structurally valid approvals publish a controlled failure; an expired root-claimed update first runs Task 6 recovery, and never receives a refreshed approval timestamp.
- Extended installer and OTA/rollback unit allowlists, enablement and restart handling for the new trigger. Installer behavior is tested with captured systemctl calls.
- Diagnostics creates root-owned mode-0644 ZIPs in `ops/public/artifacts/<job_id>.zip` and an allowlisted public result. API downloads resolve only the exact completed diagnostic job artifact and reject symlinks. Repair publishes before/after checks and fixed repair IDs. `GET /admin/ops/job` adds nullable `host_result` containing only `before`, `after`, `performed`, and `failed`.
- Scheduled doctor now publishes mode-0644 public health, including validated backup status/time. API health has exactly `version`, `git_sha`, `generated_at`, `overall`, `checks`, `update`, `last_backup`; messages are generated from a local check dictionary. Arbitrary nested JSON, host error text, paths and command fields never pass through the projection.
- Hardened the existing ZIP metadata projection: Compose values are finite typed operational fields; journal timestamps/priorities/event IDs and release metadata are validated. No journal messages, argv, environment or cookies are exported.
- API audit records include Royal actor and inspection/approval/diagnostics/repair/download acceptance or rejection. Durable root command receipts retain the actor, exact request and completion outcome.
- API startup and maintenance middleware reconcile sanitized host results and read root maintenance from the RO public bind. Existing manual snapshot/restore behavior remains covered by regression tests.

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
