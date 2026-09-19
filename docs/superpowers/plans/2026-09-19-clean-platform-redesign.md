# Clean Platform Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Выпустить чистую production-версию на PostgreSQL с быстрым двухуровневым кэшем, bounded retention, аппаратными fallback-ами и двумя целостными UI-режимами.

**Architecture:** PostgreSQL становится production-хранилищем, SQLite остаётся тестовым. API использует bounded L1/L2 и точечную инвалидацию; web — memory + account-scoped IndexedDB + PWA shell. Classic и A имеют общих владельцев данных, но изолированные presentation/layout CSS.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17, React 19, TypeScript 6, IndexedDB, Vitest, Playwright, Docker Compose, systemd.

**Spec:** `docs/plans/2026-09-19-clean-platform-redesign-design.md`

## Global Constraints

- Production всегда PostgreSQL 17; SQLite не попадает в чистую production-конфигурацию.
- Старая SQLite и её данные не импортируются; итоговый артефакт — чистый установщик.
- Неподтверждённые uploads/actions, active work, users/roles/parks, inventory balances/documents и diagnostic rules не удаляются по TTL.
- Хост сохраняет не менее `max(15% раздела, 6 GiB)` свободными.
- Не использовать Redis, `docker system prune`, mandatory GPU/NPU или скрытый network dependency.
- Макет A из скриншота 2026-09-19 19:54:53 задаёт desktop-композицию task-first, а не только цвета.
- Classic и A имеют одну бизнес-логику, API, cache keys, drafts/files/camera state и permissions.
- Все операции сохраняют idempotency, bot-authored Tracker delivery, retry/outbox и видимую рассинхронизацию.
- Версия не выпускается без PostgreSQL integration, visual review, role/workflow matrix, 200-session load и 8-hour soak evidence.

## Review Focus

- Обрыв Wi-Fi после локального optimistic update: UI показывает pending/outbox, не ложный success.
- Смена роли/парка и 401/403: защищённые memory/IndexedDB entries и late responses не отображаются.
- Давление диска во время OTA/backup/outbox: protected current/previous/recovery и unconfirmed data не удаляются.
- Отсутствие NVMe/GPU/plugins: software path полностью работает на VIM4.
- 320/390/412 px с клавиатурой/камерой: sticky actions не перекрывают поля, нижнюю навигацию и permission fallback.

---

### Task 1: PostgreSQL-compatible application runtime

**Files:**
- Modify: `apps/api/pyproject.toml`, `apps/api/uv.lock`
- Modify: `apps/api/src/robopark_api/db.py`, `config.py`
- Modify: `apps/api/src/robopark_api/services/{inventory_stock,inventory_identity,inventory_counts,inventory_receipts,inventory_exports,task_timeline}.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_collaboration.py`
- Modify: `apps/api/src/robopark_api/models.py`, `apps/api/alembic/versions/*.py`
- Create: `apps/api/tests/postgres/conftest.py`, `apps/api/tests/postgres/test_schema_and_workflows.py`

**Interfaces:**
- Produces: `configure_engine(database_url: str) -> Engine`; dialect-neutral upserts and row locks; a PostgreSQL test marker/profile.
- Consumes: existing SQLAlchemy models, Alembic head and application write barrier.

- [ ] **Step 1: Add failing PostgreSQL integration tests.** Start disposable PostgreSQL 17, run Alembic from empty DB, assert every table/index exists, then run concurrent inventory decrement, idempotent Tracker collaboration and repeatable-read export. Assert one stock decrement, one message/action and a consistent export snapshot.
- [ ] **Step 2: Verify RED.** Run `./scripts/verify.sh api-postgres`; expected failure is missing driver and SQLite-only collaboration insert/snapshot code.
- [ ] **Step 3: Add `psycopg[binary]>=3.2,<4` and dialect-neutral engine configuration.** PostgreSQL uses bounded QueuePool with pre-ping/recycle; SQLite keeps its existing test pragmas. Replace direct `sqlite_insert` in collaboration with dialect-selected insert; use PostgreSQL `FOR UPDATE` and `ON CONFLICT` branches already established by inventory services.
- [ ] **Step 4: Make schema history portable.** Add `postgresql_where` wherever only `sqlite_where` exists and replace boolean server defaults `text("1")/text("0")` with dialect-safe `true()/false()` in a forward migration without rewriting prior installed SQLite history.
- [ ] **Step 5: Verify GREEN.** Run `./scripts/verify.sh api-postgres`, then `./scripts/verify.sh api`; expected all PostgreSQL integration cases and existing API tests pass.
- [ ] **Step 6: Commit.** `git commit -m "feat(db): support PostgreSQL production runtime"`.

### Task 2: Clean PostgreSQL install, backup, restore and host profiles

**Files:**
- Modify: `deploy/docker-compose.yml`, `deploy/host.env.example`
- Modify: `deploy/host/robopark_host/{runtime,release,updater,restore,doctor,compose}.py`
- Replace PostgreSQL path in: `deploy/backup-snapshot.py`, `apps/api/src/robopark_api/services/ops/{context,snapshot,runner,reconcile}.py`
- Modify: `deploy/installer/install.sh`, `deploy/installer/lib/*.py`, `deploy/systemd/*`
- Test: `tests/host/test_{runtime,updater,restore,doctor}.py`, `apps/api/tests/test_ops_{snapshot,runner}.py`

**Interfaces:**
- Produces: `DatabaseProfile` with DSN, dump/restore commands and health checks; Compose service `db`; clean reinstall command that requires local confirmation before deleting the named Robopark data root.
- Consumes: PostgreSQL runtime from Task 1 and existing atomic maintenance/cutover barriers.

- [ ] **Step 1: Write failing host tests.** Assert production compose has PostgreSQL 17 healthcheck and persistent volume, API waits for it, no production SQLite URL exists, backup contains a custom-format dump, restore verifies it, and clean reinstall refuses broad/symlinked/unresolved targets.
- [ ] **Step 2: Verify RED.** Run `pytest -q tests/host apps/api/tests/test_ops_snapshot.py apps/api/tests/test_ops_runner.py`; expected failures name SQLite capability/hardcoded DB paths.
- [ ] **Step 3: Implement PostgreSQL Compose and adaptive resources.** Generate a VIM4-safe default (bounded connections/memory) and larger Orin profile from detected RAM/CPU. Keep DB local-only and health-gated; prefer an explicitly selected NVMe data root, never silently move existing data.
- [ ] **Step 4: Replace snapshots with `pg_dump -Fc` and `pg_restore`.** Validate dump list, Alembic head and readiness. Protect current/previous/recovery artifacts. Candidate smoke uses an isolated candidate database/schema and never production credentials/data.
- [ ] **Step 5: Implement clean reinstall.** Stop only named Robopark units/containers, confirm exact resolved data root, remove only owned Robopark data, create secrets/volumes, migrate empty PostgreSQL, seed, start and verify local API/web/DB/Tuna independently.
- [ ] **Step 6: Verify GREEN and rollback safety.** Run host/API ops suites plus packaging tests; inject DB start, dump, restore and readiness failures and assert the installer reports a recoverable state without partial publication.
- [ ] **Step 7: Commit.** `git commit -m "feat(host): install Robopark on PostgreSQL"`.

### Task 3: Server and device cache architecture

**Files:**
- Modify: `apps/api/src/robopark_api/services/{response_cache,live_merge,tracker_cache,emergency_cache}.py`
- Create: `apps/api/src/robopark_api/services/cache_policy.py`, `cache_metrics.py`
- Modify: API routers returning large GET resources and `routers/changes.py`
- Modify: `apps/web/src/lib/resource.ts`, `changeFeed.ts`, `api.ts`
- Create: `apps/web/src/lib/indexedResourceStore.ts`, `cachePolicy.ts`
- Modify: `apps/web/scripts/sw-template.js`, `apps/web/nginx.conf`
- Test: API cache tests; `apps/web/src/lib/{resource,indexedResourceStore,changeFeed}.test.ts`; SW tests.

**Interfaces:**
- Produces: `CachePolicy(ttl_seconds, stale_seconds, max_entries, max_bytes, persistence)`; `IndexedResourceStore.open(accountScope)`; targeted invalidation events `{scope, resource, ids, revision}`.
- Consumes: authorization fingerprint, park/resource keys, existing single-flight and change revision store.

- [ ] **Step 1: Write failing cache-contract tests.** Cover cross-worker single-flight, byte/entry limits, targeted issue invalidation, stale-if-error, ETag/304, IndexedDB reload, account/permission isolation, 401/403 purge, quota eviction and service-worker API exclusion.
- [ ] **Step 2: Verify RED.** Run focused API and web cache suites; expected failures include broad Tracker list clearing and missing IndexedDB tier.
- [ ] **Step 3: Introduce policy and metrics.** Every family reports hit/miss/load/error/eviction/bytes/entries; shared blobs include schema revision and bounded age. Replace `clear()` after issue mutation with affected issue/comments/counts/list projection invalidation.
- [ ] **Step 4: Add IndexedDB stale-while-revalidate.** Hydrate memory before network, namespace by account+permission+park+schema, retain last-good during background failure, purge unauthorized scopes and ignore late generations. Persist bounded thumbnails, not unbounded originals.
- [ ] **Step 5: Replace poll storms with change-feed invalidation.** Visible screens revalidate affected keys; hidden tabs stop timers. Backoff/jitter and Retry-After survive focus/online events.
- [ ] **Step 6: Verify GREEN.** Run all cache/SW tests and a 200-viewer request-count test; switching routes or UI mode with fresh data must generate zero duplicate GETs.
- [ ] **Step 7: Commit.** `git commit -m "feat(cache): add bounded server and device caching"`.

### Task 4: Unified retention, leak observation and hardware capability probes

**Files:**
- Modify: `deploy/host/robopark_host/{retention,image_retention,doctor,runtime}.py`
- Modify: `apps/api/src/robopark_api/services/{cache_cleanup,report_attachments,tracker_outbox}.py`
- Create: `apps/api/src/robopark_api/services/storage_retention.py`, `deploy/host/robopark_host/capabilities.py`
- Modify: host-health API/schema and `apps/web/src/components/admin/HostHealthPanel.tsx`
- Test: API cleanup tests, `tests/host/test_retention.py`, capability and pressure tests.

**Interfaces:**
- Produces: `HostCapabilities`, `StorageBudget`, dry-run/execute retention report and host-health fields for cache/storage/RSS/FD/capabilities.
- Consumes: protected artifact inventory, Tracker delivery state, PostgreSQL dump retention and cache metrics.

- [ ] **Step 1: Write failing safety/pressure tests.** Fill named test roots with cache, logs, confirmed/unconfirmed uploads, current/previous releases and recovery dumps; assert deletion order and protected survivors. Probe VIM4, New VIM4, Orin and generic ARM fixtures.
- [ ] **Step 2: Verify RED.** Run focused host/API cleanup tests; expected failure is missing unified budget and capability model.
- [ ] **Step 3: Implement budgets and TTLs from the spec.** Enforce free-space floor; batch deletes; expose dry-run. Never traverse unknown roots or follow symlinks. Rotate logs, caches, thumbnails, diagnostics and confirmed Tracker copies; keep primary data.
- [ ] **Step 4: Add leak observations.** Record process RSS trend, open FDs, tasks/threads, cache bytes, DB pool use and directory sizes. On sustained memory pressure evict caches first; surface failure instead of unconditional restart loops.
- [ ] **Step 5: Add optional acceleration.** Detect devices/plugins; choose hardware JPEG thumbnailer only after a health probe and fall back to libjpeg/software. CUDA/NPU never becomes a startup dependency.
- [ ] **Step 6: Verify GREEN.** Run pressure, symlink, interrupted cleanup and fallback tests; doctor output identifies the exact bounded category and last cleanup.
- [ ] **Step 7: Commit.** `git commit -m "feat(host): bound storage and detect acceleration"`.

### Task 5: Isolate Classic and A presentation foundations

**Files:**
- Modify: `apps/web/src/app/interface/{InterfaceModeProvider,interfaceModeStore,TaskFirstLayout,interface-a,classic-robot-check}.tsx|ts|css`
- Create: `apps/web/src/app/interface/{ClassicShell,TaskFirstShell,interfaceTokens}.tsx|css`
- Modify: `apps/web/src/app/shell/{AppShell.tsx,AppShell.css}`
- Modify: `apps/web/src/design-system/layout/{PageLayout.tsx,PageLayout.css}`, `apps/web/src/index.css`
- Test: interface provider/shell/layout tests and `e2e/operational/interface-mode.spec.ts`.

**Interfaces:**
- Produces: `PresentationShell` contract with navigation/header/content/context/action slots; bootstrapped account-scoped mode without first-paint flash.
- Consumes: shared router, resource store, ParkProvider, drafts/mutation counter and route manifest.

- [ ] **Step 1: Add failing structural tests.** Assert A mounts TaskFirstShell zones, Classic mounts ClassicShell, switching preserves one input/File owner, no duplicate loader, failed A chunk returns the full Classic DOM, and 320–1440 widths have one gutter owner.
- [ ] **Step 2: Verify RED.** Run provider/shell/layout suites and interface Playwright checks; expected failures show A as global CSS over Classic markup.
- [ ] **Step 3: Introduce explicit shells and tokens.** Move only reset/theme/a11y tokens to shared CSS. Scope every structural selector beneath the selected shell. Remove global A overrides for legacy `.panel/.page` and eliminate double PageShell/PageLayout padding.
- [ ] **Step 4: Bootstrap mode before authenticated paint.** Load account preference as part of auth bootstrap, keep neutral pre-auth state, preserve deferred switching during mutation, and make lazy failure switch both state and DOM presentation.
- [ ] **Step 5: Verify GREEN.** Run tests and inspect representative Classic/A desktop/mobile screenshots in light/dark; assert no classic flash, overflow or clipped nav labels.
- [ ] **Step 6: Commit.** `git commit -m "refactor(web): isolate Classic and interface A"`.

### Task 6: Rebuild task and robot workflows to the promised A composition

**Files:**
- Modify/split: `apps/web/src/domains/work/IssueWorkbench.tsx` and CSS/tests
- Modify: tracker task components, chat/photo/camera, parts and shift handoff components
- Modify: `apps/web/src/domains/robots/{RobotPage,RobotCheckWorkspace,robot-check}.tsx|css`
- Test: work/robot unit tests and operational Playwright task/robot/camera specs.

**Interfaces:**
- Produces: shared `TaskController`/`RobotCheckController` state consumed by Classic and A presentations; A center/context/action composition matching the approved screenshot.
- Consumes: cache from Task 3, presentation slots from Task 5, existing workflow/idempotency/permissions.

- [ ] **Step 1: Write failing screenshot and workflow tests.** Desktop A asserts left rail, central repair/check/chat sequence, right robot/operator context and bottom primary action. Mobile asserts disclosure context and sticky action. Add mechanic→operator cycle, external Tracker close, component injection, SLA, one-photo close, camera fallback and no reload after send.
- [ ] **Step 2: Verify RED.** Run work/robot operational suite; structural screenshot must fail against current recolored Classic.
- [ ] **Step 3: Extract controllers without changing behavior.** Lift task/robot data, drafts, File, MediaStream and actions from presentation branches; both views consume the same instances and cache keys.
- [ ] **Step 4: Build exact A task composition.** Use real task/robot data; keep repair steps, parts, comment, photo, shift handoff, operator context and primary state action. Strip Tracker control markup (`<[`, `<{`) before rendering while retaining meaningful text.
- [ ] **Step 5: Rebuild A robot check.** Desktop uses robot visual with automatic error/readout placement plus diagnostic blocks; mobile stacks them. Preserve two batteries, speed, disk, LTE/type/two SIM, error mapping/ignore/settings, camera/scan and map.
- [ ] **Step 6: Restore Classic task/robot baseline.** Classic keeps agreed hierarchy and functions without A structural CSS; fix warnings, technical collapse labels, duplicate VIN and bottom-nav clipping.
- [ ] **Step 7: Verify GREEN.** Run unit/E2E/screenshot matrix at 320/390/412/899/1440 and light/dark; manually inspect promised task A comparison and physical-camera fallback contract.
- [ ] **Step 8: Commit.** `git commit -m "feat(web): rebuild task-first work and robot UX"`.

### Task 7: Complete every route, role and functional gap

**Files:**
- Modify presentations under `apps/web/src/domains/{shift,inventory,campaigns,analytics,insights,management,diagnostics}`
- Modify legacy routes/components under `apps/web/src/pages`, `apps/web/src/components/{admin,reports,parks}`
- Modify: routing/access/nav manifests and relevant API routers/services where browser tests expose functional defects
- Test: route-role-layout and operational suites for all domains.

**Interfaces:**
- Produces: route coverage manifest listing Classic component, A component, roles, nested states and test IDs for every reachable route.
- Consumes: shared shells/controllers/cache/permissions from Tasks 3–6.

- [ ] **Step 1: Generate a failing coverage audit.** Enumerate every route plus nested tab/dialog/form/file/empty/error/stale/denied state. Fail if A falls back to unreviewed Classic markup or if a role-visible action lacks an API permission assertion.
- [ ] **Step 2: Verify RED.** Run the coverage audit; current Admin, Reports, Campaigns, management, diagnostics and operator parks must be reported as incomplete A presentations.
- [ ] **Step 3: Rebuild by workflow domain.** Overview, inventory, reports, campaigns, analytics, admin/users/roles/parks/settings and diagnostic editors each get A composition and restored Classic presentation over shared state. Preserve all approved functions, including report deletion, royal/admin rights, operator inventory read-only, global parts catalog and print/export rules.
- [ ] **Step 4: Run browser role cycles.** Mechanic, operator, driver, admin, royal and a restricted custom role traverse all allowed routes. Assert correct denial, menu contents, park scope, last activity/device/IP display and no protected stale content.
- [ ] **Step 5: Fix functional defects found by cycles.** Each defect first receives a failing test in its owning domain; implement the smallest shared logic fix, never a mode-only permission workaround.
- [ ] **Step 6: Verify GREEN.** Full web unit/E2E, API role/workflow suites, accessibility, overflow, focus, camera/file and navigation history tests pass in both modes.
- [ ] **Step 7: Commit.** `git commit -m "feat(web): complete dual-interface workflow coverage"`.

### Task 8: Capacity, soak, clean packaging and final review

**Files:**
- Modify: `scripts/capacity_{app,benchmark}.py`, `scripts/capacity-gate.py`
- Create: `scripts/cache_soak.py`, `apps/web/e2e/operational/soak.spec.ts`
- Modify: release metadata/version files, `scripts/release_pack.py`, installer/operations docs
- Create: final acceptance report under `docs/product-completion/`.

**Interfaces:**
- Produces: machine-readable load/soak/cleanup reports and one clean signed installer archive.
- Consumes: complete runtime/UI and metrics from Tasks 1–7.

- [ ] **Step 1: Add acceptance gates.** Fail packaging unless PostgreSQL integration, host suites, full API/web suites, production build, route/role coverage, screenshot review ledger, 200-session load and soak reports are current for HEAD.
- [ ] **Step 2: Run cold/warm 200-session load.** Use isolated local integrations with Wi-Fi delay/loss profile; capture p50/p95/p99, RPS/errors, bytes, upstream calls, cache hit ratio, DB pool, CPU/RSS/disk. Require zero unexpected 5xx/data leaks/duplicate mutations; record measured limits instead of inventing latency targets.
- [ ] **Step 3: Run 8-hour soak.** Repeat navigation, mode switches, task/robot/photo/camera/cache operations and background jobs. Compare settled RSS/FD/timer/subscription/object-URL/media-track/cache/storage counts after warm-up and at finish; fail unbounded monotonic growth.
- [ ] **Step 4: Re-run browser visual review.** Compare A task desktop to the approved composition and inspect representative route/role/theme/viewport screenshots. Record every accepted deviation with reason; unresolved structural mismatch blocks release.
- [ ] **Step 5: Run independent whole-branch code review.** Fix all Critical/Important findings, re-run affected suites and one scoped re-review.
- [ ] **Step 6: Build and inspect the clean installer.** Assert no SQLite DB, OTA residue, diagnostics, secrets, dev tests or generated load data in payload; verify signature/checksums and clean-install contents for Armbian 26 ARM64 and Ubuntu 22 ARM64.
- [ ] **Step 7: Commit release metadata.** `git commit -m "chore(release): prepare clean PostgreSQL installer"`.
