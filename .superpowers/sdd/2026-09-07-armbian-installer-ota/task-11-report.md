# Task 11 report — Armbian E2E, capacity gate and operator guide

## Scope and implementation

- Added 51 installer-to-update acceptance scenarios in `tests/host/test_end_to_end_update.py`, with `e2e_support.py` and two release-change fixtures. They exercise the actual installer, CLI dispatcher, root command consumer, updater/rollback, stable launcher, doctor/repair and GitHub discovery. External OS/process/filesystem/power-loss, Docker/systemd and network adapters are simulated; no updater/doctor implementation is replaced by a test stub.
- Successful candidate changes Python/npm dependency declarations and lockfiles, API/web Dockerfiles, migration, verification script, systemd app/Tuna units, Tuna script and host CLI; asserts changed authenticated files, successor launcher, digest images, root/API mount boundaries and subsequent simulated boot.
- Covered signature tampering, build/test/smoke rejection, migration failure, local health rollback, public/Tuna degraded publication, GitHub outage/explicit approval, low disk, 35 durable journal phases including all rollback phases, both versions broken with preserved snapshot and later recovery, diagnostic secret exclusion, installer readiness interruption/resume/idempotence. Four independent spawned API writer processes attempt DML against a shared SQLite fixture while the real host maintenance marker is projected into the simulated API mount view; no row is written.
- Added `scripts/capacity-gate.py`: private mode-0600 owner-checked JSON input, HTTPS/loopback-only URL, no credential CLI flags, 200 concurrent clients, 30 s warmup + 600 s measurement defaults, safe GET mix, optional newly-created inactive test park, explicit environment `ALLOW_ISOLATED_WRITES=true` plus both config opt-ins, `finally` deactivation, no existing-park writes. Output contains only aggregates; failure to clean up blocks PASS.
- Capacity reports nearest-rank p50/p95/p99, throughput, non-2xx/transport error rate and DB-lock/event-loop/OOM/restart pattern counts. Report evaluation rejects extra fields and arbitrary text; without target server evidence the result cannot be PASS. It models 200 clients using one supplied test session, not 200 distinct accounts. Server-log counters are observed failures, not an internal event-loop latency instrument.
- Common runtime renderer applies 2-worker/3 GiB API profile for 8 GiB hosts and 4-worker/12 GiB for 32 GiB hosts; web 256 MiB, pids 512, container nofile 65536. Host services have LimitNOFILE 65536 and TasksMax 4096. Actual capacity remains unmeasured on target.
- Added Russian `deploy/INSTALL-ARMBIAN-RU.md`, `deploy/CAPACITY-RU.md`, links in both READMEs. Uses actual VERSION **0.1.0**, pre-extraction signature verification, private answer-file handling, autostart, Royal ZIP/GitHub approvals, backup/recovery, exact repair allowlist and target checklist. Every target result is explicitly **НЕ ВЫПОЛНЕНО**.

## Production defects found by E2E (parent explicitly authorized fixes)

1. First real installer-to-OTA attempt failed `compose_config_missing`: bootstrap created a regular `current-compose.json`, updater required a symlink. Bootstrap now durably writes `ops/state/compose/bootstrap-<manifest-digest>.json`, then atomically publishes the symlink; invalid ordinary files/foreign targets fail closed.
2. OTA's duplicate Compose renderer dropped `OPS_HOST_ROOT`, release public-key RO mount and worker-profile authority. Installation and OTA now share `production_config` and `pin_images`; production image tags are resolved after build to immutable image IDs. Reboot and rollback preserve the same storage/configuration contract.
3. Doctor falsely reported a missing manifest before the first update because it required a previous-release link. Previous is optional on a fresh host; when present it is strictly checked.
4. Doctor searched only legacy `PUBLIC_URL` while installer stored canonical `CORS_ORIGINS`. Installed-host checks now reuse the updater's strict trusted-file origin parser; legacy fallback remains for manual layouts.

## RED / GREEN evidence

- Capacity harness: 24 failures because the executable was absent; initial implementation passed 24. Added missing explicit-write opt-in regression: one expected failure, then pass. Added report-field injection regression: one expected failure, then pass. Final capacity tests: **27 passed**.
- Profile tests: two expected failures for missing container limits, then pass. Added canonical database/attachment environment assertions: two failures, then pass. Final profile tests: **2 passed**.
- E2E first success scenario reproduced `compose_config_missing` before production changes; after shared renderer/symlink fix the success scenario passed. Negative scenarios exposed the fresh-host doctor defect; fixed and rechecked.
- Existing mock Compose consumers were updated to model `docker image inspect` and explicit read_only=false for writable exchange mounts. Bootstrap test fixtures now supply the actual required host.env. No production guard was weakened for test setup.

## Local verification

- `UV_CACHE_DIR=/private/tmp/robopark-task11-uv ./scripts/verify.sh api`: **768 passed**, ruff + format pass; one existing pytest warning. Three API import-order-only fixes match parent's separate primary commit `843d48b`; they are restored/excluded from Task 11 commits.
- `npm_config_cache=/private/tmp/robopark-task11-npm npm_config_offline=true ./scripts/verify.sh web`: **1313 passed / 85 test files**, lint, TypeScript/Vite build and check-nav (27 route IDs) pass. npm reported blocked optional fsevents install scripts; the web gate completed successfully.
- `tests/host/test_packaging.py`: **109 passed**; deterministic packages, signature/metadata/path/secret boundary tests included.
- Focused runtime/updater/profile regression set: **90 passed**; focused systemd/bootstrap set: **14 passed**.
- Full canonical host gate: **PASS / exit 0 — 499 pytest tests plus 36 installer scenarios**. This includes all 50 E2E cases and all 35 interruption phases. An earlier broad run identified two obsolete bootstrap fixtures missing host.env; both were corrected before this final passing run.
- Changed Python files checked/formatted using the API project's Ruff configuration. `git diff --check`: pass.
- Pattern-only tracked/new-file scan: **664 files, 0 findings** for actual PEM private-key headers and long GitHub/AWS credential patterns, no secret values printed. No production configuration or `.release-secrets` was read. This is not a claim that actual target diagnostics have been scanned against all configured secrets.
- Disposable-key packaging of the real repository source was run twice for release ZIP and installer tar.gz with fixed SOURCE_DATE_EPOCH. All four archives independently verified, corresponding bytes matched. Synthetic test provenance metadata was used; these are not production release artifacts. Temporary private key and output directory were deleted automatically.
  - Ephemeral release SHA-256: `d5e1504234c10f428b1cb74530f0a8948a4372a45604c6a22acce5a6963af260`
  - Ephemeral installer SHA-256: `a22c4cc025d68e1958894184b46d953fce6a4faf21db57cf1f07f5b5a3c07e77`

## Target-only / release integration gates

**НЕ ВЫПОЛНЕНО**: real ARM64/AMD64 Linux installer, systemd service lifecycle/reboot, actual Docker/Compose builds and image runtime, Tuna HTTPS, Royal login on device, dependency/migration/unit/updater replacement on device, real outage/recovery, deliberate failed-OTA rollback, real diagnostic secret scan, 200-user reads/writes and complete server evidence.

`./scripts/verify.sh docker` was actually invoked and exited **127**, `docker is required for the docker verification target`. Therefore `./scripts/verify.sh all` cannot be called green on this Mac. Docker, systemd and target capacity were not silently skipped or claimed successful.

The dependency-change JSON is a filesystem/signature/cutover probe for fake Docker adapters, not a buildable distributable candidate. The operator checklist separately requires a genuinely buildable signed release changing real resolved dependencies and successful target builds. Production versioned artifacts must be built and independently verified **after integration in the primary worktree**, by the parent/release operator with the production key. This task never opened/copied production key material and does not publish anything.

## Review follow-up — cleanup authority and successor execution

- Confirmed the capacity cleanup defect: a successful create response containing a rejected ID, such as the string `"999"`, assigned that value before validation and the `finally` block then PATCHed it. The harness now validates a local candidate as an exact integer in `1..2**63-1` before assigning cleanup state. Rejected IDs and non-object response bodies cannot trigger any PATCH. Existing cleanup-failure handling still prevents PASS.
- Capacity RED: **11 failed / 28 passed** before the fix. GREEN: **39 passed**. Regression inputs cover string, boolean, zero, negative, null, float, integer overflow, object, list, and non-object JSON response bodies; each asserts no request follows the create POST.
- Confirmed that the previous E2E process adapter reused the already imported CLI implementation. Added executable source to the authenticated successor fixture, recording its version, source path and command only for reconciliation/recovery. The new regression first failed because no successor marker was written despite a successful update.
- The process adapter now executes the actual staged/current `robopark` entrypoint via `runpy`, imports its CLI/updater/dependencies from that host-tools directory with a fresh package namespace, and restores the caller's modules/argv/path/exception context in `finally`. Only external command/HTTP boundaries are injected. The signed successor's own code now proves both `update --reconcile` and post-boot `update --recover` execute from the installed 0.1.1 source. This remains an in-process OS/process simulation; real Linux exec/systemd/reboot gates remain **НЕ ВЫПОЛНЕНО**.
- Focused update/successor/rollback/GitHub verification: **10 passed**. Final canonical `UV_CACHE_DIR=/private/tmp/robopark-task11-uv ./scripts/verify.sh host`: **PASS / exit 0**, **512 pytest tests in 174.13 s**, then **36 installer scenarios in 84.393 s**. Includes **39 capacity**, **51 E2E**, all **35 interruption phases**, packaging and the other host tests.
- Ruff check, Ruff format check and `git diff --check`: **PASS**. API/web were not rerun for this follow-up because their code and dependencies did not change; earlier results above remain historical. No target-only gate was run, no production artifacts built, and no production key read.

## Commits

- Production E2E blocker fixes + their tests: **8e7dfa6** (`fix(deploy): align installer and OTA runtime contracts`).
- Task 11 capacity harness/operator docs/report: the following `test(deploy): add capacity gate and Armbian operator guide` commit (hash supplied in handoff).
