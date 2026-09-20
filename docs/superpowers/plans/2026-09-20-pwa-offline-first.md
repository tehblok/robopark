# Offline-first PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить существующую PWA в быстрый локальный рабочий клиент с безопасной очередью действий, полным офлайн-циклом задачи, уведомлениями и графиком смен.

**Architecture:** Расширить существующий разделённый по аккаунту `IndexedResourceStore` отдельными хранилищами сущностей, медиа и действий. Один клиентский sync engine отправляет зависимые идемпотентные операции в пакетный FastAPI endpoint, а сервер переиспользует `ReliableAction`, task lifecycle и inventory services. Service Worker хранит только оболочку; защищённые данные остаются в IndexedDB.

**Tech Stack:** React 19, TypeScript 6, IndexedDB, Service Worker, Web Locks, BroadcastChannel, Web Workers, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Vitest, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-09-20-pwa-offline-first-design.md`

## Global Constraints

- Данные разделяются по account, role, permissions и park; отзыв доступа очищает прежний охват.
- Повтор действия с тем же idempotency key не создаёт дубль.
- Незавершённые действия и вложения не удаляются автоматической очисткой.
- Service Worker не кеширует авторизованные API responses.
- На слабой сети одновременно выполняются не более двух операций.
- Offline и sync не перезагружают страницу и не сбрасывают форму, прокрутку или вкладку.
- Комментарии и фото объединяются автоматически; статусы, закрытие и остатки требуют ручного решения конфликта.
- Критический сценарий не зависит от Background Sync или BarcodeDetector.
- Оба интерфейсных стиля, светлая и тёмная темы используют один data/sync engine.

## Review Focus

- Поздний ответ предыдущего аккаунта не должен восстановить его данные после logout/login; закрепить в Task 1.
- Две вкладки не должны одновременно отправить одну операцию; закрепить в Task 3.
- Обрыв после серверного commit до получения ответа не должен повторно списать запчасть; закрепить в Task 2 и Task 4.
- Обновление Service Worker не должно удалить незавершённое фото или очередь; закрепить в Task 6.
- Заполненное хранилище должно остановить новую запись с понятной ошибкой, сохранив ранее ожидающие действия; закрепить в Task 1 и Task 5.

---

### Task 1: Версионированная локальная база и область доступа

**Files:**
- Create: `apps/web/src/pwa/offlineDb.ts`
- Create: `apps/web/src/pwa/offlineDb.test.ts`
- Create: `apps/web/src/pwa/offlineTypes.ts`
- Modify: `apps/web/src/lib/deviceResourceCache.ts`
- Modify: `apps/web/src/lib/deviceResourceCache.test.ts`

**Interfaces:**
- Produces: `OfflineScope`, `OfflineAction`, `OfflineMedia`, `openOfflineDb(scope)`, `purgeOfflineScope()`, atomic `transaction()` and quota-aware `cleanup()`.
- Consumes: current account/role/permissions/park fingerprint rules from `deviceResourceCache.ts`.

- [ ] **Step 1: Write failing storage tests**

  Add Vitest cases using `fake-indexeddb` for schema migration, atomic entity/action writes, scope purge, quota failure, protected pending actions, and a late generation write after scope change.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/web && npm test -- src/pwa/offlineDb.test.ts src/lib/deviceResourceCache.test.ts`

  Expected: FAIL because `openOfflineDb` and offline stores do not exist.

- [ ] **Step 3: Implement minimal database**

  Define stores `entities`, `actions`, `media`, `revisions`, `meta`; action indexes by state, resource and creation time; media indexes by action and upload state. Store a generation token with every handle and reject writes after activation changes. Cleanup deletes expired confirmed actions and LRU entity snapshots, never `local|ready|sending|conflict|attention` actions or unconfirmed media.

- [ ] **Step 4: Verify GREEN and regression**

  Run: `cd apps/web && npm test -- src/pwa/offlineDb.test.ts src/lib/deviceResourceCache.test.ts src/lib/resource.test.ts`

  Expected: PASS with no unhandled IndexedDB requests.

- [ ] **Step 5: Commit**

  Commit: `feat(pwa): add scoped offline database`

### Task 2: Пакетный sync API и серверные ревизии

**Files:**
- Create: `apps/api/src/robopark_api/sync_schemas.py`
- Create: `apps/api/src/robopark_api/services/offline_sync.py`
- Create: `apps/api/src/robopark_api/routers/sync.py`
- Create: `apps/api/tests/test_offline_sync.py`
- Create: `apps/api/alembic/versions/0033_offline_sync_receipts.py`
- Modify: `apps/api/src/robopark_api/task_workflow_models.py`
- Modify: `apps/api/src/robopark_api/main.py`

**Interfaces:**
- Produces: `POST /sync/batch` accepting `{device_id, known_revisions, actions[]}` and returning `{results[], deltas, revisions, revoked_scopes[]}`.
- Consumes: `begin_action`, task lifecycle commands, inventory writeoff, RBAC and `change_revision_store`.

- [ ] **Step 1: Write failing API tests**

  Cover a comment followed by review, duplicate batch replay, same key/different payload conflict, unauthorized park, server-closed task, stale stock revision, mixed success/conflict results, and retry after response loss.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/api && uv run pytest tests/test_offline_sync.py -q`

  Expected: FAIL with `/sync/batch` missing.

- [ ] **Step 3: Add receipt model and migration**

  Add `OfflineSyncReceipt(id, actor_user_id, device_id, client_action_id, payload_hash, result_json, created_at)` with a unique actor/device/action constraint. Preserve existing `ReliableAction` idempotency for Tracker delivery; receipts only make the batch boundary replayable.

- [ ] **Step 4: Implement dispatcher**

  Validate every action independently against current RBAC and park scope, order by declared dependencies, call existing lifecycle/inventory services, commit each dependency group atomically, and serialize stable result codes `confirmed`, `conflict`, `attention`, `rejected`. Return only deltas currently visible to the actor.

- [ ] **Step 5: Verify GREEN and migration**

  Run: `cd apps/api && uv run pytest tests/test_offline_sync.py tests/test_task_lifecycle.py tests/test_inventory.py tests/test_models_migration.py -q`

  Expected: PASS; replay assertions show one movement and one transition.

- [ ] **Step 6: Commit**

  Commit: `feat(api): add idempotent batch synchronization`

### Task 3: Единственный клиентский sync engine

**Files:**
- Create: `apps/web/src/pwa/syncEngine.ts`
- Create: `apps/web/src/pwa/syncEngine.test.ts`
- Create: `apps/web/src/pwa/syncCoordinator.ts`
- Create: `apps/web/src/pwa/syncCoordinator.test.ts`
- Create: `apps/web/src/pwa/SyncProvider.tsx`
- Create: `apps/web/src/pwa/SyncProvider.test.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/App.tsx`

**Interfaces:**
- Produces: `enqueueAction(input)`, `syncNow(reason)`, `subscribeSyncState()`, `cancelAction(id)`, `resolveConflict(id, resolution)` and `useSync()`.
- Consumes: Task 1 `OfflineDb`, Task 2 `POST /sync/batch`.

- [ ] **Step 1: Write failing engine tests**

  Test dependency ordering, one leader across two coordinators, expired lease takeover, max concurrency 2 on weak connection, online/focus wakeup, retry with jitter, confirmed removal retention, conflict pause and revoked-scope purge.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/web && npm test -- src/pwa/syncEngine.test.ts src/pwa/syncCoordinator.test.ts src/pwa/SyncProvider.test.tsx`

  Expected: FAIL because the engine modules do not exist.

- [ ] **Step 3: Implement coordinator and engine**

  Prefer `navigator.locks`; fall back to a short IndexedDB lease. Broadcast state via `BroadcastChannel`. Use one scheduled pump, an abort controller per request, exponential backoff with jitter, and `navigator.connection` only as an optional hint. Never infer success from a lost response; replay the same client action id.

- [ ] **Step 4: Add API and provider integration**

  Add typed `syncBatch` to `api.ts`, mount one `SyncProvider` inside authenticated application scope, and dispose listeners, timers, channels and requests on scope change.

- [ ] **Step 5: Verify GREEN and leak assertions**

  Run: `cd apps/web && npm test -- src/pwa/syncEngine.test.ts src/pwa/syncCoordinator.test.ts src/pwa/SyncProvider.test.tsx src/api.test.ts`

  Expected: PASS; listener/timer spies return to baseline after unmount.

- [ ] **Step 6: Commit**

  Commit: `feat(pwa): add single offline sync engine`

### Task 4: Офлайн-цикл задачи и склада

**Files:**
- Create: `apps/web/src/domains/work/offlineTaskActions.ts`
- Create: `apps/web/src/domains/work/offlineTaskActions.test.ts`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.test.tsx`
- Modify: `apps/web/src/domains/work/TaskTimeline.tsx`
- Modify: `apps/web/src/domains/work/TaskSyncStatus.tsx`

**Interfaces:**
- Produces: optimistic local comment, handoff, defect/photo review and inventory writeoff operations with stable dependencies.
- Consumes: Task 3 `useSync()` and cached task/inventory entities.

- [ ] **Step 1: Write failing workflow tests**

  Exercise offline comment, refreshed comment before close, exactly one photo, defect code, parts writeoff, handoff, submit review, browser restart, reconnect, duplicate taps, insufficient stale stock and Tracker-closed task.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/web && npm test -- src/domains/work/offlineTaskActions.test.ts src/domains/work/IssueWorkbench.test.tsx src/domains/inventory/TaskPartsPanel.test.tsx`

  Expected: FAIL because mutations still require a live request.

- [ ] **Step 3: Route supported mutations through the queue**

  Persist the optimistic timeline item and action in one IndexedDB transaction. Encode dependencies `media -> comment -> review`; keep the same UUID on retries. Reserve stock locally but mark it pending until the server confirms. On conflict restore the canonical server stock and open the sync attention panel.

- [ ] **Step 4: Replace technical status noise**

  Show only `Сохранено на устройстве`, `Отправляется` or `Нужно внимание` at the action level; confirmed actions disappear from routine UI. Preserve forms and scroll during delta application.

- [ ] **Step 5: Verify GREEN and online regression**

  Run: `cd apps/web && npm test -- src/domains/work src/domains/inventory/TaskPartsPanel.test.tsx`

  Expected: PASS for offline and existing online paths.

- [ ] **Step 6: Commit**

  Commit: `feat(work): support complete offline task cycle`

### Task 5: Медиа, камера и локальное сканирование

**Files:**
- Create: `apps/api/src/robopark_api/media_schemas.py`
- Create: `apps/api/src/robopark_api/services/media_uploads.py`
- Create: `apps/api/src/robopark_api/routers/media_uploads.py`
- Create: `apps/api/alembic/versions/0034_resumable_media_uploads.py`
- Create: `apps/api/tests/test_media_uploads.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/web/src/pwa/mediaPipeline.ts`
- Create: `apps/web/src/pwa/mediaPipeline.test.ts`
- Create: `apps/web/src/pwa/media.worker.ts`
- Create: `apps/web/src/pwa/resumableUpload.ts`
- Create: `apps/web/src/pwa/resumableUpload.test.ts`
- Modify: `apps/web/src/domains/robots/RobotScanner.tsx`
- Modify: `apps/web/src/domains/robots/RobotScanner.test.tsx`
- Modify: `apps/web/src/domains/robots/robotScannerFallback.ts`
- Modify: `apps/web/src/domains/robots/robotScannerFallback.test.ts`
- Modify: `apps/web/src/domains/work/SubmitReviewForm.tsx`
- Modify: `apps/web/src/domains/work/SubmitReviewForm.test.tsx`
- Modify: `apps/web/src/api.ts`

**Interfaces:**
- Produces: `POST /media/uploads`, idempotent `PUT /media/uploads/{id}/chunks/{offset}`, `POST /media/uploads/{id}/complete`, `prepareImage(file)`, resumable `uploadMedia(mediaId)`, local scan with jsQR fallback and manual entry.
- Consumes: Task 1 media store and Task 3 sync dependencies.

- [ ] **Step 1: Write failing media tests**

  Cover server session ownership, offset/checksum validation, replayed chunks, finalize idempotency and abandoned-chunk cleanup. Cover client EXIF orientation, preview lifecycle, size ceiling, checksum stability, resume after chunk interruption, quota rejection that preserves existing media, denied camera, missing BarcodeDetector, released tracks and revoked object URLs.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/api && uv run pytest tests/test_media_uploads.py -q` and `cd apps/web && npm test -- src/pwa/mediaPipeline.test.ts src/pwa/resumableUpload.test.ts src/domains/robots/RobotScanner.test.tsx src/domains/robots/robotScannerFallback.test.ts src/domains/work/SubmitReviewForm.test.tsx`

  Expected: FAIL because upload sessions and worker pipeline do not exist.

- [ ] **Step 3: Implement bounded server upload sessions**

  Persist owner, expected size, MIME, checksum, received offset, expiry and completion state. Accept only the next bounded chunk or an exact replay, stream it to a per-session staging file, verify final checksum, then atomically move it into existing task attachment staging. Reject cross-user and expired sessions; cleanup only abandoned or confirmed temporary chunks.

- [ ] **Step 4: Implement worker pipeline and client upload session**

  Generate WebP/JPEG working image and preview off the UI thread, persist checksum and blob before returning success, upload bounded chunks with offset confirmation, and free source buffers after server acknowledgement.

- [ ] **Step 5: Unify camera, file and scanner fallbacks**

  Keep both `capture` and normal file selection on phone. Scanner tries BarcodeDetector, then bundled jsQR frames, then manual input. Always stop media tracks on success, cancel, route exit and component unmount.

- [ ] **Step 6: Verify GREEN**

  Run the RED command again.

  Expected: PASS with URL/track cleanup assertions.

- [ ] **Step 7: Commit**

  Commit: `feat(pwa): add resilient local media pipeline`

### Task 6: Service Worker, установка и центр синхронизации

**Files:**
- Modify: `apps/web/scripts/sw-template.js`
- Modify: `apps/web/scripts/build-sw.mjs`
- Modify: `apps/web/scripts/build-sw.test.mjs`
- Modify: `apps/web/public/manifest.webmanifest`
- Modify: `apps/web/src/pwa/registerServiceWorker.ts`
- Modify: `apps/web/src/pwa/registerServiceWorker.test.ts`
- Create: `apps/web/src/pwa/SyncCenter.tsx`
- Create: `apps/web/src/pwa/SyncCenter.test.tsx`
- Create: `apps/web/src/pwa/ShareTargetInbox.tsx`
- Create: `apps/web/src/pwa/ShareTargetInbox.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx`

**Interfaces:**
- Produces: cached navigation shell, deferred safe activation, install/share shortcuts and human-readable sync center.
- Consumes: Task 3 sync state; does not read protected data from CacheStorage.

- [ ] **Step 1: Write failing SW and UI tests**

  Assert cached `index.html` shell on offline navigation, no `/api` caching, old worker remains while pending actions exist, explicit safe activation, manifest shortcuts/share target, and sync center action/conflict/storage summaries.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/web && npm test -- src/pwa/registerServiceWorker.test.ts src/pwa/SyncCenter.test.tsx && node --test scripts/build-sw.test.mjs`

  Expected: FAIL because navigate currently falls back only to `offline.html` and no sync center exists.

- [ ] **Step 3: Implement shell-first navigation and update handshake**

  Precache the built index and immutable assets. Serve cached shell immediately for navigation and update it in background. Add worker messages `UPDATE_READY` and `ACTIVATE_WHEN_SAFE`; activate only after sync reports no sending transaction, while persisted pending actions/media remain untouched.

- [ ] **Step 4: Add install surface and sync center**

  Add manifest shortcuts for My Tasks, scanner and inventory plus a same-origin share target. Persist an incoming shared photo as an unassigned local draft, then let the user attach it to a task or report; discard it explicitly if cancelled. Expose one compact shell indicator and a detailed panel for queue, conflicts, local storage and retry/cancel controls.

- [ ] **Step 5: Verify GREEN and build**

  Run: `cd apps/web && npm test -- src/pwa/registerServiceWorker.test.ts src/pwa/SyncCenter.test.tsx && node --test scripts/build-sw.test.mjs && npm run build`

  Expected: PASS and generated `dist/sw.js` contains no API cache handler.

- [ ] **Step 6: Commit**

  Commit: `feat(pwa): make the application shell offline ready`

### Task 7: Уведомления и график смен

**Files:**
- Create: `apps/api/src/robopark_api/schedule_models.py`
- Create: `apps/api/src/robopark_api/schedule_schemas.py`
- Create: `apps/api/src/robopark_api/services/schedules.py`
- Create: `apps/api/src/robopark_api/routers/schedules.py`
- Create: `apps/api/src/robopark_api/routers/push.py`
- Create: `apps/api/alembic/versions/0035_schedules_and_push.py`
- Create: `apps/api/tests/test_schedules.py`
- Create: `apps/api/tests/test_push.py`
- Create: `apps/web/src/domains/shift/ScheduleWorkspace.tsx`
- Create: `apps/web/src/domains/shift/ScheduleWorkspace.test.tsx`
- Create: `apps/web/src/pwa/notifications.ts`
- Create: `apps/web/src/pwa/notifications.test.ts`
- Create: `apps/web/src/pwa/NotificationCenter.tsx`
- Create: `apps/web/src/pwa/NotificationCenter.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/api/src/robopark_api/main.py`

**Interfaces:**
- Produces: self-service schedule CRUD; admin scoped read; royal edit/bulk assign; push subscription and internal notification inbox.
- Consumes: account/park RBAC, Task 2 revision stream and Task 6 Service Worker.

- [ ] **Step 1: Write failing schedule and permission tests**

  Cover self shift/vacation/sick range, overlap warning, admin read-only accessible parks, royal all-park edit/copy/bulk assignment, audit author/time and silent viewing.

- [ ] **Step 2: Write failing push routing tests**

  Assert role/event matrix from the spec, on-shift targeting for mechanic/driver, internal inbox fallback, user category preferences and mandatory internal royal system alerts.

- [ ] **Step 3: Verify RED**

  Run: `cd apps/api && uv run pytest tests/test_schedules.py tests/test_push.py -q`

  Expected: FAIL because schedule and push routes do not exist.

- [ ] **Step 4: Implement models, services and routes**

  Store schedule entries as explicit Europe/Moscow intervals with owner, kind, start/end, source, series id and audit fields; bulk/recurring edits create bounded occurrences instead of an unbounded recurrence evaluator. Store endpoint subscriptions encrypted or hashed as appropriate and remove expired endpoints after push rejection. Emit compact event ids; never include protected task text in push payload.

- [ ] **Step 5: Write failing frontend tests and implement UI**

  Run first: `cd apps/web && npm test -- src/domains/shift/ScheduleWorkspace.test.tsx src/pwa/notifications.test.ts src/app/routing/routeManifest.test.ts`

  Expected: FAIL because the route and clients do not exist.

  Implement phone list/editor, desktop week/month views, filters, permission-aware controls and an internal notification inbox. Request system permission only from an explicit user click.

- [ ] **Step 6: Verify GREEN**

  Run both API and frontend commands from Steps 3 and 5.

  Expected: PASS for every role matrix case.

- [ ] **Step 7: Commit**

  Commit: `feat: add schedules and role-aware notifications`

### Task 8: Ограничение ресурсов, нагрузка и окончательная приёмка

**Files:**
- Create: `apps/web/src/pwa/performanceBudget.test.ts`
- Create: `apps/web/src/pwa/clientTelemetry.ts`
- Create: `apps/web/src/pwa/clientTelemetry.test.ts`
- Create: `apps/web/e2e/pwa-offline.spec.ts`
- Create: `apps/api/src/robopark_api/routers/client_telemetry.py`
- Create: `apps/api/tests/test_client_telemetry.py`
- Create: `apps/api/tests/test_sync_load.py`
- Modify: `apps/api/src/robopark_api/services/cache_cleanup.py`
- Modify: `apps/api/tests/test_cache_cleanup.py`
- Modify: `docs/product-completion/acceptance-matrix.md`

**Interfaces:**
- Produces: bounded cleanup, regression evidence and acceptance mapping.
- Consumes: all earlier tasks.

- [ ] **Step 1: Add failing cleanup and resource-bound tests**

  Test expiration of confirmed receipts, push endpoints, stale schedule projections and temporary upload chunks while retaining every pending action/media file. Assert one sync engine, bounded listeners/timers, virtualized long lists, no duplicate route request storm, sampled aggregate telemetry without task text/photo data, and immediate delivery only for critical sync failures.

- [ ] **Step 2: Verify RED**

  Run: `cd apps/api && uv run pytest tests/test_cache_cleanup.py tests/test_sync_load.py -q` and `cd apps/web && npm test -- src/pwa/performanceBudget.test.ts`

  Expected: FAIL on missing new retention classes or budgets.

- [ ] **Step 3: Implement bounded cleanup and adaptive limits**

  Extend the existing cleanup loop with batched deletes and explicit protected states. Apply LRU/TTL device limits and one/two request concurrency based on connection stability without making Network Information API mandatory. Add sampled, batched client metrics for startup latency, queue length, storage size, retries and migration failures; reject arbitrary payload fields server-side.

- [ ] **Step 4: Add end-to-end offline acceptance**

  Cover installable manifest, warm offline navigation, full mechanic workflow, restart/reconnect, conflict, account switch, camera fallback and service-worker update with pending work.

- [ ] **Step 5: Run focused acceptance**

  Run: `cd apps/web && npx playwright test e2e/pwa-offline.spec.ts --project=chromium`

  Expected: PASS on desktop and mobile viewport cases.

- [ ] **Step 6: Run complete bounded verification**

  Run: `./scripts/verify.sh api`, `cd apps/web && npm test`, `cd apps/web && npm run build`, `cd apps/web && npm run check:contrast`, and the repository's bounded load command for 200 virtual users.

  Expected: all commands exit 0; memory and request counts stay within assertions. Do not run an unattended multi-hour soak unless separately requested.

- [ ] **Step 7: Update acceptance matrix and commit**

  Record commands, results and covered requirements in `docs/product-completion/acceptance-matrix.md`.

  Commit: `test: verify offline PWA stability and load limits`
