# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Исправить пункты 2–10 системного аудита, сохранив отключённую проверку подписи локального OTA и не выполняя длительные проверки без разрешения пользователя.

**Architecture:** Общие межпроцессные состояния переносятся в PostgreSQL и обслуживаются единственным lease-owned фоновым контуром. Быстрые CI-проверки отделяются от opt-in soak/load/VM gate, а PWA получает настоящий production smoke против собранного `dist`. Evidence остаётся source-bound и не подменяется после изменений.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL 17, React 19, Vite 8, Playwright, service worker, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-20-audit-remediation-design.md`

## Global Constraints

- Не менять отключённую проверку подписи локального OTA.
- Не запускать полный API/web suite, soak, load benchmark, Docker build или VM/installer test без отдельного согласия.
- Применять TDD и запускать только короткие тесты затронутых модулей, статические проверки и короткий production-PWA smoke.
- Не объявлять устаревшие evidence действительными и не создавать фиктивный `PASS`.
- Новые фоновые процессы обязаны быть bounded, lease-owned и безопасными при 2–4 Uvicorn worker.
- Не отправлять IP стороннему сервису без явного включения провайдера.

## Review Focus

- Два API worker одновременно видят новую задачу: уведомление создаётся ровно один раз.
- Инцидент `down → healthy → down`: второе падение создаёт новый occurrence, непрерывный сбой не спамит.
- Недоступный Web Push endpoint: внутреннее событие сохраняется, доставка ограничена общим дедлайном.
- Рестарт API и смена worker не сбрасывают login/register throttle.
- Production service worker не кэширует API, приватные ответы или вложения.

---

### Task 1: Общая PostgreSQL-схема фоновых событий и throttle

**Files:**
- Create: `apps/api/alembic/versions/0036_audit_remediation_state.py`
- Modify: `apps/api/src/robopark_api/schedule_models.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Test: `apps/api/tests/test_models_migration.py`
- Test: `apps/api/tests/postgres/test_schema_and_workflows.py`

**Interfaces:**
- Создать модели для lease/cursor новой Tracker-очереди, occurrence системного инцидента и общего login throttle.
- Хранить только хэш ключа throttle, счётчик/окно/блокировку и timestamps.
- Индексы обязаны поддерживать атомарный upsert и bounded cleanup.

- [ ] Написать failing migration/model tests для новых таблиц, ограничений и индексов.
- [ ] Запустить только соответствующие SQLite migration tests и убедиться в ожидаемом падении.
- [ ] Добавить migration и ORM-модели без изменения существующих контрактов.
- [ ] Запустить `test_models_migration.py` и один короткий PostgreSQL schema test, если локальный контейнер уже доступен; иначе отметить PostgreSQL execution pending.
- [ ] Выполнить `ruff check` только изменённых Python-файлов.
- [ ] Commit: `feat(db): add shared notification and throttle state`.

### Task 2: Надёжные системные уведомления и ограниченная Web Push доставка

**Files:**
- Modify: `apps/api/src/robopark_api/routers/push.py`
- Modify: `apps/api/src/robopark_api/services/system_notifications.py`
- Modify: `apps/api/src/robopark_api/config.py`
- Test: `apps/api/tests/test_push.py`
- Test: `apps/api/tests/test_system_notifications.py`

**Interfaces:**
- `PushService.emit` сохраняет внутренние события независимо от Web Push.
- Incident occurrence идентифицируется циклом здоровья, а не бессрочной сигнатурой проверки.
- `_deliver` использует ограниченный пул, batch size и общий deadline из settings.

- [ ] Написать failing test на `down → healthy → down` и непрерывный `down`.
- [ ] Написать failing test на ограниченную конкурентность, deadline и сохранение внутреннего уведомления при сбое доставки.
- [ ] Реализовать occurrence lifecycle и bounded delivery.
- [ ] Запустить только `test_push.py` и `test_system_notifications.py`.
- [ ] Выполнить `ruff check` изменённых файлов.
- [ ] Commit: `fix(push): make incidents repeatable and delivery bounded`.

### Task 3: Серверный poller новых Tracker-задач

**Files:**
- Create: `apps/api/src/robopark_api/services/tracker_notifications.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/config.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_read.py`
- Test: `apps/api/tests/test_tracker_notifications.py`
- Test: `apps/api/tests/test_tracker_read.py`

**Interfaces:**
- Фоновый poller запускается только владельцем существующего lifespan lease.
- Он использует Tracker token/cache, ограниченный page size и постоянный cursor/dedupe из Task 1.
- Клиентский `GET /tracker/issues` больше не отвечает за создание событий.
- Ошибка upstream не уничтожает cursor и не завершает loop.

- [ ] Написать failing tests: уведомление без GET, два worker/один event, restart/cursor replay, Tracker failure/recovery.
- [ ] Реализовать poller и подключить его к lifecycle с bounded shutdown.
- [ ] Удалить побочный эффект уведомления из `tracker_read.py` и обновить его тест.
- [ ] Запустить только новые tests и затронутые tests `tracker_read`.
- [ ] Выполнить `ruff check` изменённых файлов.
- [ ] Commit: `feat(tracker): notify new tasks from server poller`.

### Task 4: Межпроцессный login/register throttle

**Files:**
- Modify: `apps/api/src/robopark_api/services/login_throttle.py`
- Modify: `apps/api/src/robopark_api/routers/auth.py`
- Modify: `apps/api/src/robopark_api/services/cache_cleanup.py`
- Test: `apps/api/tests/test_login_throttle.py`
- Test: `apps/api/tests/test_auth.py`
- Test: `apps/api/tests/postgres/test_schema_and_workflows.py`

**Interfaces:**
- Throttle принимает SQLAlchemy session/factory и выполняет атомарный update/upsert.
- Ключ `username|ip` хранится только как SHA-256.
- Успешный вход сбрасывает ключ, cleanup удаляет истёкшие строки пакетами.

- [ ] Написать failing tests на сохранение блокировки между экземплярами сервиса и на hash-only storage.
- [ ] Добавить короткий PostgreSQL concurrency test атомарного счётчика.
- [ ] Реализовать DB-backed throttle и bounded cleanup.
- [ ] Запустить только throttle/auth tests; PostgreSQL test — только если контейнер уже доступен.
- [ ] Выполнить `ruff check` изменённых файлов.
- [ ] Commit: `fix(auth): share login throttle across workers`.

### Task 5: Явно управляемая IP-геолокация

**Files:**
- Modify: `apps/api/src/robopark_api/config.py`
- Modify: `apps/api/src/robopark_api/services/ip_location.py`
- Modify: `deploy/installer/lib/configure.py`
- Modify: `deploy/host.env.example`
- Test: `apps/api/tests/test_user_activity.py`
- Test: `apps/api/tests/test_config.py`

**Interfaces:**
- `IP_GEO_PROVIDER=off|ipwhois`, default `off`.
- `off` не выполняет сеть, но сохраняет IP/device activity.
- Неизвестное значение отклоняется configuration validation.

- [ ] Написать failing tests на default-off, explicit provider и отсутствие HTTP-вызова.
- [ ] Реализовать provider dispatch и installer/env defaults.
- [ ] Запустить только config/user-activity tests.
- [ ] Выполнить `ruff check` изменённых файлов и `sh -n` затронутого installer shell при наличии.
- [ ] Commit: `fix(privacy): require explicit IP geolocation provider`.

### Task 6: Разделение browser CI, soak и production PWA smoke

**Files:**
- Modify: `apps/web/playwright.config.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/scripts/playwright-linux.sh`
- Modify: `apps/web/e2e/operational/soak.spec.ts`
- Create: `apps/web/playwright.pwa.config.ts`
- Create: `apps/web/e2e-production/pwa-production.spec.ts`
- Modify: `.github/workflows/ci.yml`
- Test: `apps/web/scripts/build-sw.test.mjs`

**Interfaces:**
- `test:e2e:linux` исключает soak.
- Отдельная команда soak требует output/duration явно.
- `test:e2e:pwa` обслуживает `dist` и проверяет установленный worker в Chromium.

- [ ] Написать failing config/discovery test, подтверждающий разделение обычных E2E и soak.
- [ ] Написать production browser smoke на регистрацию SW, offline shell и отсутствие API/private cache.
- [ ] Реализовать отдельные configs/scripts и CI steps.
- [ ] Запустить только config/unit tests и короткий production PWA smoke; не запускать остальные E2E.
- [ ] Выполнить web lint для затронутых файлов.
- [ ] Commit: `test(pwa): verify production service worker separately`.

### Task 7: Короткие PostgreSQL и lifecycle integration contracts

**Files:**
- Modify: `apps/api/tests/postgres/test_schema_and_workflows.py`
- Create: `apps/api/tests/test_lifespan_jobs.py`
- Modify: `scripts/verify.sh`
- Modify: `docs/product-completion/acceptance-matrix.md`

**Interfaces:**
- PostgreSQL integration проверяет duplicate sync, media completion, schedules/push и существующий inventory race.
- Lifecycle test использует короткие интервалы и управляемые fakes, без sleep длительнее нескольких секунд.
- `verify.sh` сохраняет отдельные fast/full/load/soak targets и ничего долгого не добавляет в fast target.

- [ ] Добавить тесты критических PostgreSQL HTTP-контрактов на migration head.
- [ ] Добавить short lifecycle test для lease, maintenance barrier и shutdown.
- [ ] Разделить verify targets и документировать их продолжительность/назначение.
- [ ] Запустить только lifecycle test; PostgreSQL tests запускать только при уже доступном контейнере.
- [ ] Выполнить `sh -n scripts/verify.sh` и `ruff check` новых Python tests.
- [ ] Commit: `test(ops): add short production contract gates`.

### Task 8: Frontend correctness warnings, документация и release status

**Files:**
- Modify: `apps/web/src/lib/resource.ts`
- Modify: `apps/web/src/domains/robots/RobotRegistryList.tsx`
- Modify: `apps/web/src/components/tracker/RepairSla.tsx`
- Modify: дополнительные файлы только для подтверждённых функциональных lint warnings
- Modify: `README.md`
- Modify: `deploy/README.md`
- Modify: `deploy/INSTALL-ARMBIAN-RU.md`
- Modify: `docs/product-completion/PROGRESS.md`
- Modify: `docs/product-completion/release-evidence.json` only to mark current evidence stale if schema supports it; never to forge `PASS`
- Test: соответствующие ближайшие Vitest files

**Interfaces:**
- Render не читает mutable refs и не вызывает недетерминированное `Date.now()` напрямую.
- Документация называет текущую версию/PostgreSQL и отделяет выполненные проверки от ожидающих разрешения.

- [ ] Добавить или уточнить короткие component tests для каждого исправляемого warning.
- [ ] Исправить только функциональные React warnings и запустить ближайшие test files.
- [ ] Обновить README/ops/status без изменения подписи OTA.
- [ ] Запустить `npm run lint`, `npm run check-nav` и точечные Vitest files; не запускать полный web suite.
- [ ] Запустить acceptance validator и зафиксировать ожидаемый fail-closed результат до разрешённого полного прогона.
- [ ] Commit: `docs: align release status with current verification`.

### Task 9: Итоговая короткая проверка и независимое ревью

**Files:**
- Modify only defects found by review within Tasks 1–8.

- [ ] Запустить только объединённый набор ранее использованных точечных tests, `ruff check` изменённых Python-файлов, `npm run lint`, `npm run check-nav`, `sh -n` затронутых shell scripts и короткий production PWA smoke.
- [ ] Не запускать full API/web suite, load, soak, Docker build или installer/VM.
- [ ] Провести независимое code review diff относительно `1386096`.
- [ ] Исправить подтверждённые findings и повторить только затронутые короткие tests.
- [ ] Зафиксировать список неисполненных длительных gates и не создавать OTA.
