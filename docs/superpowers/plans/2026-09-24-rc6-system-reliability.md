# Robopark 0.2.0-rc.6 System Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `0.2.0-rc.6` as a data-preserving local update and clean installer with a fast client-first UI, durable integrations, managed host operations, and one polished classic interface.

**Architecture:** FastAPI persists authoritative local actions and PostgreSQL outbox records; a dedicated worker performs Tracker polling/delivery, push, cleanup and metrics. React/PWA keeps scoped projections and safe drafts on the device, while a typed root host agent performs only audited maintenance operations.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL, React/TypeScript/Vite, IndexedDB, Service Worker/Web Push, Docker Compose, systemd, pytest, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-24-rc6-system-reliability-design.md`

## Global Constraints

- Target version is `0.2.0-rc.6`; release channel is `rc`; migration head is `0038_inventory_photo_cleanup`.
- Upgrade from installed `0.2.0-rc.5` must preserve PostgreSQL volumes, users, roles, parks, inventory, schedules, reports, attachments, secrets, configuration, trusted signing key and backup-device identity.
- API request processes must not own long-running Tracker, push, cleanup or metrics loops.
- Tracker uses bounded polling: 15 seconds active, 60 seconds quiet, exponential backoff capped at five minutes.
- Optimistic UI renders in the next frame; client micro-batches use 100-250 ms; private API responses never enter a shared HTTP cache.
- Claims, stock mutations, handoff and review require server acknowledgement; safe drafts remain available offline.
- Only the classic interface remains, with system/light/dark themes and compact/comfortable density.
- Required viewport checks: 360, 390, 412, 768, 1024, 1366 and 1920 pixels.
- Long soak, 200-user load, destructive installer rehearsal and live-host update require separate user approval.
- No arbitrary shell execution is exposed by the application or host agent.

## Review Focus

- Interrupted update after migration starts must resume or stop safely without replacing preserved data; Task 2 owns the recovery tests.
- Worker restart with pending Tracker and Web Push deliveries must lose no event and create no duplicate mutation; Tasks 3 and 5 own these tests.
- Account, role or park switch must never reveal another scope's cached records or photos; Task 8 owns the isolation tests.
- Safari/Chrome without `BarcodeDetector`, denied camera permission, and released camera tracks must all have usable fallbacks; Task 10 owns these tests.
- Destructive maintenance with missing TOTP, wrong recovery code, stale operation ID or unverified backup must fail before host mutation; Tasks 13 and 14 own these tests.

---

### Task 1: Make release identity and migration contracts fail closed

**Files:**
- Create: `scripts/check-release-migrations.py`
- Modify: `VERSION`, `apps/web/package.json`, `apps/web/package-lock.json`, `apps/api/pyproject.toml`, `apps/api/uv.lock`, `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `deploy/release-metadata.json`, `deploy/migration-policy.json`, `scripts/check-release-version.py`, `scripts/generate-release-notes.py`, `scripts/verify.sh`
- Create: `docs/releases/0.2.0-rc.6.md`
- Test: `tests/host/test_release_version.py`, `tests/host/test_release_metadata.py`, `tests/host/test_migration_graph.py`

**Interfaces:**
- Consumes: Alembic `ScriptDirectory.get_current_head()`.
- Produces: `check_release_migrations(root: Path) -> list[str]`, zero findings only when runtime, metadata and policy all equal `0038_inventory_photo_cleanup`.

- [ ] **Step 1: Write failing contract tests** asserting the repository version is `0.2.0-rc.6`, channel is `rc`, the actual Alembic head equals both release files, and release-note generation reads `VERSION` instead of a hard-coded target.
- [ ] **Step 2: Run RED:** `pytest -q tests/host/test_release_version.py tests/host/test_release_metadata.py tests/host/test_migration_graph.py`; expect rc5/0036 mismatches.
- [ ] **Step 3: Implement the checker and synchronize authorities:**
  ```python
  def check_release_migrations(root: Path) -> list[str]:
      actual = ScriptDirectory.from_config(alembic_config(root)).get_current_head()
      declared = json.loads((root / "deploy/release-metadata.json").read_text())["migration_head"]
      policy = json.loads((root / "deploy/migration-policy.json").read_text())["target_head"]
      return [] if actual == declared == policy else [f"actual={actual} metadata={declared} policy={policy}"]
  ```
  Set accepted source heads to `0036_audit_remediation_state`, `0037_claim_workflow_visibility`, and `0038_inventory_photo_cleanup` only.
- [ ] **Step 4: Run PASS:** the Step 2 command plus `./scripts/verify.sh host`; expect all short release gates to pass.
- [ ] **Step 5: Commit:** `git commit -am "fix(release): align rc6 migration contract"` after adding the new checker and release note.

### Task 2: Prove data-preserving local upgrade and recovery

**Files:**
- Modify: `deploy/installer/START.sh`, `deploy/installer/lib/local-update.py`, `deploy/installer/lib/install-release.py`
- Modify: `deploy/host/robopark_host/updater.py`, `deploy/host/robopark_host/rollback.py`
- Test: `tests/host/test_end_to_end_update.py`, `tests/host/test_update_recovery.py`, `tests/host/test_restore.py`, `tests/host/test_release_acceptance.py`

**Interfaces:**
- Consumes: signed rc6 manifest from Task 1.
- Produces: `inspect_installation(root) -> InstallationState`; update mode preserves named paths/volumes and accepts 0036-0038 source heads.

- [ ] **Step 1: Add failing fixtures** for rc5 data/config/secrets/trusted-key/backup UUID, an interruption before cutover, an interruption after migration start, and an unknown source head.
- [ ] **Step 2: Run RED:** `pytest -q tests/host/test_end_to_end_update.py tests/host/test_update_recovery.py tests/host/test_restore.py`; expect preservation/resume assertions to fail.
- [ ] **Step 3: Implement preflight-before-mutation and durable journal:**
  ```python
  @dataclass(frozen=True)
  class InstallationState:
      mode: Literal["clean", "upgrade", "repair", "remove"]
      current_version: str | None
      migration_head: str | None
      preserved_paths: tuple[Path, ...]
  ```
  Display action selection before verification, snapshot before quiescing writes, never recreate data volumes during upgrade, and permit automatic rollback only before writes resume.
- [ ] **Step 4: Run PASS:** Step 2 command and `pytest -q tests/host/test_release_acceptance.py`; expect preserved fixture hashes and recovery journal assertions to pass.
- [ ] **Step 5: Commit:** `git commit -am "fix(installer): preserve rc5 data during rc6 update"`.

### Task 3: Separate request serving from background work

**Files:**
- Create: `apps/api/src/robopark_api/worker.py`, `apps/api/src/robopark_api/services/worker_runtime.py`
- Modify: `apps/api/src/robopark_api/main.py`, `deploy/docker-compose.yml`
- Test: `apps/api/tests/test_worker_runtime.py`, `apps/api/tests/test_lifespan_jobs.py`, `tests/host/test_systemd_units.py`

**Interfaces:**
- Produces: `WorkerRuntime.start(stop: Event) -> None`, `worker_health() -> WorkerHealth`; compose service `worker` using the API image.

- [ ] **Step 1: Add failing tests** proving API lifespan starts no job loops, worker starts each loop once, a second worker cannot acquire leases, and web/API remain healthy if worker is unavailable.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_worker_runtime.py apps/api/tests/test_lifespan_jobs.py`.
- [ ] **Step 3: Move loop ownership** into `WorkerRuntime` while retaining PostgreSQL leases and cooperative stop events; add `worker` with the same image/database and a distinct command.
- [ ] **Step 4: Run PASS:** Step 2 command plus `docker compose -f deploy/docker-compose.yml config`; expect one worker service and no API loop startup.
- [ ] **Step 5: Commit:** `git commit -am "refactor(runtime): isolate background worker"`.

### Task 4: Add adaptive Tracker polling and unified synchronization health

**Files:**
- Modify: `apps/api/src/robopark_api/services/tracker_notifications.py`, `apps/api/src/robopark_api/services/tracker_outbox.py`, `apps/api/src/robopark_api/routers/admin_health.py`
- Create: `apps/api/src/robopark_api/services/sync_health.py`
- Modify: `apps/api/src/robopark_api/ops_schemas.py`
- Test: `apps/api/tests/test_tracker_notifications.py`, `apps/api/tests/test_tracker_outbox.py`, `apps/api/tests/test_admin_health.py`

**Interfaces:**
- Produces: `poll_delay(active: bool, failures: int) -> timedelta`; `SyncHealthOut` with cursor age, oldest pending action, retry counts, last success/error and worker lease state.

- [ ] **Step 1: Add failing tests** for 15/60 second polling, five-minute backoff cap, targeted refresh after accepted mutation, closure reconciliation, and scoped admin/royal health output.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_tracker_notifications.py apps/api/tests/test_tracker_outbox.py apps/api/tests/test_admin_health.py`.
- [ ] **Step 3: Implement exact scheduling and projection:**
  ```python
  def poll_delay(active: bool, failures: int) -> timedelta:
      base = 15 if active else 60
      return timedelta(seconds=min(300, base * (2 ** min(failures, 5))))
  ```
  Reconcile externally closed/resolved/cancelled tickets and release stale local claims.
- [ ] **Step 4: Run PASS:** Step 2 command; expect cursor and queue metrics without exposing payloads.
- [ ] **Step 5: Commit:** `git commit -am "feat(sync): expose adaptive tracker health"`.

### Task 5: Make notification delivery durable and schedule-aware

**Files:**
- Create: `apps/api/alembic/versions/0039_notification_delivery.py`
- Create: `apps/api/src/robopark_api/notification_delivery_models.py`, `apps/api/src/robopark_api/services/notification_delivery.py`
- Modify: `apps/api/src/robopark_api/routers/push.py`, `apps/api/src/robopark_api/services/system_notifications.py`, `apps/api/src/robopark_api/services/schedules.py`
- Test: `apps/api/tests/test_notification_delivery.py`, `apps/api/tests/test_push.py`, `apps/api/tests/test_system_notifications.py`

**Interfaces:**
- Produces: `NotificationDelivery(event_id, channel, state, attempts, next_attempt_at, expires_at, idempotency_key)` and `eligible_recipients(event, at) -> list[User]`.

- [ ] **Step 1: Add failing tests** for crash/restart replay, duplicate worker delivery, 404/410 subscription removal, transient retry for 24 hours, schedule precedence, 4-on/4-off days and royal critical delivery outside shift.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_notification_delivery.py apps/api/tests/test_push.py apps/api/tests/test_system_notifications.py`.
- [ ] **Step 3: Replace the in-memory delivery queue** with leased database rows and neutral channel adapters (`in_app`, `web_push`, future `telegram`); persist event before attempts.
- [ ] **Step 4: Run PASS:** Step 2 command plus migration upgrade test; expect one durable attempt per idempotency key.
- [ ] **Step 5: Commit:** `git commit -am "feat(notifications): persist delivery and routing"`.

### Task 6: Enforce the complete claim-to-review workflow

**Files:**
- Modify: `apps/api/src/robopark_api/services/reliable_actions.py`, `apps/api/src/robopark_api/services/tracker_outbox.py`, `apps/api/src/robopark_api/task_workflow_models.py`
- Modify: `apps/api/src/robopark_api/services/inventory_stock.py`, `apps/api/src/robopark_api/routers/sync.py`
- Test: `apps/api/tests/test_claim_workflow.py`, `apps/api/tests/test_tracker_outbox.py`, `apps/api/tests/test_offline_sync.py`

**Interfaces:**
- Produces: idempotent dependency chain `assign_operator -> diag_complete -> ensure_component -> start -> activate_claim`; review requires comment, defect code and exactly one photo.

- [ ] **Step 1: Add failing tests** for exact claim order, empty/non-empty components, duplicate requests, conflicting owner, atomic stock decrement, handoff ownership, review validation, operator return reason and external closure.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_claim_workflow.py apps/api/tests/test_tracker_outbox.py apps/api/tests/test_offline_sync.py`.
- [ ] **Step 3: Implement the state machine** using stable action keys and transaction-scoped locks; keep technical action events in audit only and emit concise business chat events.
- [ ] **Step 4: Run PASS:** Step 2 command; expect no duplicate Tracker action or stock decrement.
- [ ] **Step 5: Commit:** `git commit -am "fix(workflow): make repair lifecycle idempotent"`.

### Task 7: Add server metrics, online presence and bounded retention

**Files:**
- Create: `apps/api/alembic/versions/0040_system_metrics_presence.py`
- Create: `apps/api/src/robopark_api/services/system_observability.py`, `apps/api/src/robopark_api/routers/admin_system.py`
- Modify: `apps/api/src/robopark_api/services/storage_retention.py`, `apps/api/src/robopark_api/routers/admin_users.py`, `apps/api/src/robopark_api/main.py`
- Test: `apps/api/tests/test_system_observability.py`, `apps/api/tests/test_cache_cleanup.py`, `apps/api/tests/test_admin_users.py`

**Interfaces:**
- Produces: `/admin/system/summary`, `/admin/system/history?days=7`, `/presence/heartbeat`; online means heartbeat within two minutes.

- [ ] **Step 1: Add failing tests** for role/park online counts, seven-day aggregates, worker/Tracker/outbox/push health, 24-hour raw metrics, seven-day five-minute aggregates, every retention class in the spec, and user deletion that deactivates sessions/personal data but retains an immutable actor snapshot and refuses deletion of the last royal.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_system_observability.py apps/api/tests/test_cache_cleanup.py`.
- [ ] **Step 3: Implement bounded models and worker collectors**; never delete unsynced or unresolved records and publish only scoped data to admin while royal sees the whole system.
- [ ] **Step 4: Run PASS:** Step 2 command; assert repeat cleanup is idempotent and storage growth becomes bounded.
- [ ] **Step 5: Commit:** `git commit -am "feat(system): add presence metrics and retention"`.

### Task 8: Unify client storage, projections and optimistic batching

**Files:**
- Create: `apps/web/src/pwa/storageRegistry.ts`, `apps/web/src/pwa/clientBatcher.ts`
- Modify: `apps/web/src/pwa/offlineDb.ts`, `apps/web/src/pwa/offlineTypes.ts`, `apps/web/src/pwa/syncEngine.ts`, `apps/web/src/pwa/SyncProvider.tsx`
- Modify: `apps/web/src/lib/indexedResourceStore.ts`, `apps/web/src/shared/auth/protectedBrowserStorage.ts`
- Test: `apps/web/src/pwa/storageRegistry.test.ts`, `apps/web/src/pwa/offlineDb.test.ts`, `apps/web/src/pwa/syncEngine.test.ts`

**Interfaces:**
- Produces: `storageRegistry.inventory()`, `purgeScope(scope)`, `enqueueOptimistic(action)`, 100-250 ms batching and network-only fallback.

- [ ] **Step 1: Add failing Vitest cases** for v1->v2 preservation, account/role/park isolation, logout cleanup, 14-day projections, quota pressure, denied IndexedDB, 100 ms batching and conflict rollback.
- [ ] **Step 2: Run RED:** `npm test -- --run src/pwa/storageRegistry.test.ts src/pwa/offlineDb.test.ts src/pwa/syncEngine.test.ts` from `apps/web`.
- [ ] **Step 3: Register every IndexedDB/localStorage namespace** with owner, schema and retention; update projections before network dispatch and show status only for sending, waiting or attention.
- [ ] **Step 4: Run PASS:** Step 2 command; expect no cross-scope reads and no permanent “Сохранено” indicator.
- [ ] **Step 5: Commit:** `git commit -am "feat(pwa): unify scoped local data flow"`.

### Task 9: Preserve PWA data across safe application updates

**Files:**
- Modify: `apps/web/scripts/build-sw.mjs`, `apps/web/scripts/sw-template.js`, `apps/web/src/pwa/registerServiceWorker.ts`
- Test: `apps/web/e2e-production/pwa-production.spec.ts`, `apps/web/e2e/pwa-offline.spec.ts`

**Interfaces:**
- Consumes: pending action/media counts from Task 8.
- Produces: versioned shell activation only after `ACTIVATE_WHEN_SAFE`; private URLs excluded from CacheStorage.

- [ ] **Step 1: Add failing E2E cases** proving a waiting worker cannot activate with pending media/actions, activates after sync, removes obsolete shell caches, and preserves all scoped IndexedDB records.
- [ ] **Step 2: Run RED:** `npx playwright test e2e-production/pwa-production.spec.ts e2e/pwa-offline.spec.ts` from `apps/web`.
- [ ] **Step 3: Bind cache metadata to rc6 build identity** without changing IndexedDB scope schema; keep ordinary browser and installed PWA behavior identical.
- [ ] **Step 4: Run PASS:** repeat Step 2; expect offline shell and queued action recovery at 390 and 1440 widths.
- [ ] **Step 5: Commit:** `git commit -am "fix(pwa): activate rc6 without losing local work"`.

### Task 10: Make camera, barcode and photo workflows resilient

**Files:**
- Modify: `apps/web/src/domains/robots/RobotScanner.tsx`, `apps/web/src/domains/robots/robotScannerFallback.ts`
- Modify: `apps/web/src/pwa/media.worker.ts`, `apps/web/src/domains/work/SubmitReviewForm.tsx`, `apps/web/src/domains/inventory/InventoryManageView.tsx`
- Test: `apps/web/src/domains/robots/RobotScanner.test.tsx`, `apps/web/src/domains/work/SubmitReviewForm.test.tsx`, `apps/web/e2e/operational/robots.spec.ts`

**Interfaces:**
- Produces: rear-camera scan, lazy jsQR fallback, gallery/manual input, one completion photo, resumable 1920px/82% upload, original retained until acknowledgement.

- [ ] **Step 1: Add failing tests** for missing BarcodeDetector, denied/insecure camera, gallery/manual fallback, track release on navigation, local preview, one-photo replacement and inventory-part photo upload.
- [ ] **Step 2: Run RED:** `npm test -- --run src/domains/robots/RobotScanner.test.tsx src/domains/work/SubmitReviewForm.test.tsx` from `apps/web`.
- [ ] **Step 3: Implement deterministic capability selection** and resumable media records; bypass lossy compression for QR/documents and fall back to original when worker conversion fails.
- [ ] **Step 4: Run PASS:** Step 2 command and `npx playwright test e2e/operational/robots.spec.ts`.
- [ ] **Step 5: Commit:** `git commit -am "fix(media): harden camera and photo uploads"`.

### Task 11: Finish the classic interface across roles and platforms

**Files:**
- Modify: `apps/web/src/design-system/styles/tokens.css`, `layout.css`, `base.css`
- Modify: `apps/web/src/design-system/layout/PageLayout.css`, `MasterDetail.css`, `apps/web/src/design-system/navigation/Tabs.css`, `apps/web/src/design-system/actions/Button.css`
- Modify: `apps/web/src/domains/work/work.css`, `apps/web/src/domains/inventory/inventory.css`, affected page components under `apps/web/src/domains/`
- Test: `apps/web/e2e/operational/visual-acceptance.spec.ts`, `apps/web/e2e/operational/route-role-layout.spec.ts`, `apps/web/e2e/operational/interface-mode.spec.ts`

**Interfaces:**
- Produces: one classic mode with consistent spacing/radii/actions; no context column, no interface-A selector, no clipped/overlapping controls.

- [ ] **Step 1: Add geometry assertions** for all roles and routes at 360/390/412/768/1024/1366/1920, light/dark/system and compact/comfortable; assert task tabs/actions, park selector and sync indicator alignment.
- [ ] **Step 2: Run RED:** `npx playwright test e2e/operational/visual-acceptance.spec.ts e2e/operational/route-role-layout.spec.ts e2e/operational/interface-mode.spec.ts`.
- [ ] **Step 3: Normalize tokens and layouts:** 8px base spacing, 12px control radius, 16px panel radius, 44px minimum touch target, bounded content width, responsive grids, visually distinct primary/secondary/destructive actions; remove duplicated context and “Проверить/Запчасти/Что было сделано” blocks from chat.
- [ ] **Step 4: Run PASS:** Step 2 command on Chromium; run the critical task, robot, inventory, reports, schedule and admin journeys on Firefox/WebKit.
- [ ] **Step 5: Commit:** `git commit -am "fix(ui): unify classic layouts across devices"`.

### Task 12: Complete personal and team scheduling

**Files:**
- Modify: `apps/api/src/robopark_api/services/schedules.py`, `apps/api/src/robopark_api/routers/schedules.py`, `apps/api/src/robopark_api/schedule_schemas.py`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.tsx`, `PersonalScheduleCalendar.tsx`, `SchedulePlanner.tsx`, `ScheduleTeamGrid.tsx`, `ScheduleWorkspace.css`
- Test: `apps/api/tests/test_schedules.py`, `apps/web/src/domains/shift/ScheduleWorkspace.test.tsx`, `SchedulePlanner.test.tsx`, `ScheduleTeamGrid.test.tsx`

**Interfaces:**
- Produces: personal single/bulk shift, vacation and illness entries; reusable 4-on/4-off patterns; admin read access; hidden royal read/edit/assignment access; IANA timezone storage.

- [ ] **Step 1: Add failing tests** for individual and bulk entries, overlap warnings, 4-on/4-off generation, employee self-only mutation, admin team read, royal assignment/edit, pagination and 09:00-21:00 fallback when no schedule exists.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_schedules.py` and `npm test -- --run src/domains/shift/ScheduleWorkspace.test.tsx src/domains/shift/SchedulePlanner.test.tsx src/domains/shift/ScheduleTeamGrid.test.tsx`.
- [ ] **Step 3: Implement the calendar contracts** with local-device IANA timezone, copy/range operations and role-scoped views; keep scheduling separate from the main work screen and use it as the notification routing source.
- [ ] **Step 4: Run PASS:** repeat Step 2 and verify 360/390/1366 layouts with keyboard and touch navigation.
- [ ] **Step 5: Commit:** `git commit -am "feat(schedule): complete personal and team planning"`.

### Task 13: Protect dangerous operations with TOTP and immutable audit

**Files:**
- Create: `apps/api/alembic/versions/0041_privileged_auth.py`
- Create: `apps/api/src/robopark_api/services/privileged_auth.py`, `apps/api/src/robopark_api/routers/privileged_auth.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`, `apps/api/src/robopark_api/services/platform_settings.py`
- Test: `apps/api/tests/test_privileged_auth.py`, `apps/api/tests/test_admin_ops.py`

**Interfaces:**
- Produces: encrypted TOTP secret, ten hashed single-use recovery codes, short-lived royal reauthorization token bound to operation kind and ID.

- [ ] **Step 1: Add failing tests** for enrollment confirmation, encrypted-at-rest secret, recovery-code one-time use, last-royal protection, password+TOTP requirement and audit record on denial/success.
- [ ] **Step 2: Run RED:** `pytest -q apps/api/tests/test_privileged_auth.py apps/api/tests/test_admin_ops.py`.
- [ ] **Step 3: Implement RFC 6238 verification** with a narrowly scoped dependency, ±1 time step, encrypted secret and hashed recovery codes; keep normal product use available before setup while locking dangerous operations.
- [ ] **Step 4: Run PASS:** Step 2 command; assert wrong/stale credentials cause zero host command files.
- [ ] **Step 5: Commit:** `git commit -am "feat(security): require strong confirmation for host ops"`.

### Task 14: Expand the typed host agent, backup and USB operations

**Files:**
- Modify: `deploy/host/robopark_host/commands.py`, `cli.py`, `state.py`, `paths.py`, `retention.py`, `restore_retention.py`
- Modify: `apps/api/src/robopark_api/services/ops/host_bridge.py`, `runner.py`, `apps/api/src/robopark_api/ops_schemas.py`
- Test: `tests/host/test_host_commands.py`, `tests/host/test_host_exclusion.py`, `tests/host/test_task4_storage_capabilities.py`, `tests/host/test_restore_retention.py`

**Interfaces:**
- Produces: allow-listed operation kinds for update/reinstall/rollback, package inspection/update, service restart/reboot, backup/verify/restore, cleanup preview/execute, diagnostics and USB discover/format/select.

- [ ] **Step 1: Add failing tests** for schema rejection, duplicate operation IDs, UUID selection, removable-device validation, double-confirmed format, encrypted backup, external recovery-key requirement, verified-backup guard and protected current/previous/recovery releases.
- [ ] **Step 2: Run RED:** `pytest -q tests/host/test_host_commands.py tests/host/test_host_exclusion.py tests/host/test_task4_storage_capabilities.py tests/host/test_restore_retention.py`.
- [ ] **Step 3: Implement explicit enums and handlers** with no command-string field; write atomic progress receipts and require typed phrase plus Task 13 authorization for destructive kinds.
- [ ] **Step 4: Run PASS:** Step 2 command; inspect tests to ensure fake block devices only, with no real disk mutation.
- [ ] **Step 5: Commit:** `git commit -am "feat(host): add typed backup and maintenance operations"`.

### Task 15: Build the royal system console

**Files:**
- Create: `apps/web/src/domains/system/SystemPage.tsx`, `SystemMetrics.tsx`, `SystemOperations.tsx`, `system.css`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`, `apps/web/src/nav.ts`, `apps/web/src/opsApi.ts`
- Test: `apps/web/src/domains/system/SystemPage.test.tsx`, `apps/web/e2e/operational/admin-system.spec.ts`

**Interfaces:**
- Consumes: Tasks 4, 7, 13 and 14 APIs.
- Produces: admin read/retry view and royal update/cleanup/backup/restore/restart/configuration controls.

- [ ] **Step 1: Add failing component/E2E tests** for role scoping, current/7-day users, CPU/memory/disk/Postgres/containers/network, queue/worker/Tuna health, cleanup preview, typed confirmations, progress resume and blocked dangerous operations.
- [ ] **Step 2: Run RED:** `npm test -- --run src/domains/system/SystemPage.test.tsx && npx playwright test e2e/operational/admin-system.spec.ts` from `apps/web`.
- [ ] **Step 3: Implement the console** with polling suspended when hidden, compact charts, accessible dialogs and resumable operation progress; never expose raw command entry.
- [ ] **Step 4: Run PASS:** repeat Step 2 across Chromium and the critical confirmation flow in Firefox/WebKit.
- [ ] **Step 5: Commit:** `git commit -am "feat(admin): add managed system console"`.

### Task 16: Integrate, verify, merge and package rc6

**Files:**
- Modify: `docs/releases/0.2.0-rc.6.md`, `deploy/release-metadata.json`
- Generated after merge: signed OTA archive and checksum outside tracked source.
- Test: all short gates named below.

**Interfaces:**
- Consumes: all earlier tasks and existing release signing key.
- Produces: clean installer/OTA archive for rc6 plus SHA-256 and signature verification report.

- [ ] **Step 1: Run static and unit gates:** `./scripts/verify.sh api`, web unit tests, `npm run build`, and `./scripts/verify.sh host`; all must exit 0.
- [ ] **Step 2: Run bounded browser gates:** critical Chromium/Firefox/WebKit journeys only; do not start soak or 200-user load.
- [ ] **Step 3: Run repository consistency gates:** migration checker, version checker, artifact verifier and `git diff --check`; expect rc6/rc/0038 everywhere and no ignored source needed at runtime.
- [ ] **Step 4: Request independent whole-branch review** focused on data loss, duplicate side effects, authorization bypass, cache leakage and rollback boundaries; fix every blocker/high finding with its own regression test.
- [ ] **Step 5: Merge the reviewed branch into `main`**, rerun the short gates on the merge commit, then package/sign from that exact 40-character SHA.
- [ ] **Step 6: Verify the archive offline** and report archive path, size, SHA-256, manifest version/channel/migration head and accepted upgrade source; do not install on the host.
- [ ] **Step 7: Ask separately before** real host upgrade, destructive restore rehearsal, 200-user load or long soak.

## Self-review record

- Spec coverage: all sections 1-21 map to Tasks 1-16; future Telegram remains an adapter contract and not an implementation.
- Placeholder scan: no deferred implementation markers or unspecified error-handling steps remain.
- Type consistency: worker, sync health, notification delivery, storage registry and privileged-operation interfaces are introduced before consumers.
- Review focus: each of the five highest-risk failure classes has an owning regression-test task.
- Scope protection: archive creation is allowed; installation, destructive disk operations, long soak and 200-user load remain explicit approval gates.
