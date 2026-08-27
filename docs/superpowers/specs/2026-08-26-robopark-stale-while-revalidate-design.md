# Robopark — Stale-while-revalidate для Tracker (и смежных экранов)

**Date:** 2026-08-26  
**Status:** implemented  
**Depends on:** Phase 4/5 Tracker, UI shell, Emergency viewer  
**Plan (продолжать отсюда):** [`docs/superpowers/plans/2026-08-26-robopark-stale-while-revalidate.md`](../plans/2026-08-26-robopark-stale-while-revalidate.md)  
**Trigger:** данные из Startrek долго не появляются на экране; нужно сразу показывать последний известный снимок из памяти и обновлять после ответа API — **во всех разделах**.

## Goal

Убрать «пустой экран / скелетон на 10+ секунд» при повторных заходах на экраны, которые тянут Startrek (и аналогично медленные внешние источники). Паттерн:

1. При монтировании экрана **сразу** рисуем то, что уже есть в памяти / localStorage.
2. В фоне дергаем API; тонкий глобальный progress bar показывает, что идёт обновление.
3. Когда свежий ответ пришёл — **атомарно** подменяем UI.

Backend параллельно кэширует ответы Tracker с TTL + single-flight, чтобы повторные/параллельные запросы не били Startrek лишний раз.

## Non-goals (этот эпик)

- Tuna / публичный HTTPS туннель (host-deploy, сознательно вне демо).
- Постоянное хранение Tracker-данных в SQLite как source of truth (история блокеров уже отдельный job; SWR — про UX ответа API).
- React Query / SWR-библиотека с npm — свой тонкий `resourceStore` + `useCachedResource`.
- Offline-first / service worker.
- Push / websocket live-обновления.

---

## Chat summary (контекст сессии)

Краткая хронология того, что привело к этой спеке (transcript [ревью и демо](6f10dd01-f732-415c-b5ef-2edde532069d)):

1. **Ревью inspection + hardening** на `main` после merge: ACL Emergency VIN (`emergency_scope`), миграция plaintext-секретов, жёсткий CORS — часть была потеряна при squash-merge; восстановлено отдельным PR.
2. **Синк + демо:** `main` обновлён, зависимости, API/web с `DEV_SEED=true`.
3. **Ошибка Startrek:** фоновые job’ы (`blocker_history`, `emergency_keepalive`) ловили `ProxyError` из‑за Cursor-sandbox прокси `127.0.0.1:…`. Прямой доступ к `st-api.yandex-team.ru` есть (401 без токена — норма).
4. **Логин падал**, потому что API умирал вместе с обёрточной shell (SIGHUP после `nohup` в короткой команде).
5. **Требование «демо без Tuna, но все функции»:** добавлен `scripts/dev-demo.sh` (`start|stop|restart|status|run-api|run-web`), чистит `HTTP(S)_PROXY`, поднимает API+web; Tuna намеренно не трогается. Документировано в `README.md` и `docs/DEV-ACCOUNTS.md`.
6. **Процесс фидбека:** пользователь пишет пункты по одному → проработка → дальше; затем проход по экранам/функциям с вопросами.
7. **Пункт 1 (этот эпик):** долго нет данных Tracker на экране → показывать из памяти сразу, после ручки обновлять автоматически, **во всех разделах**. Код ядра уже лежит в рабочей копии (не закоммичен целиком) — спека фиксирует статус «сделано / осталось».

---

## Decisions

| Topic | Choice |
|-------|--------|
| UX-паттерн | Stale-while-revalidate (SWR): stale paint → background fetch → swap |
| Клиентский store | In-memory `Map` + зеркало в `localStorage` (`robopark:res:*`) |
| Индикатор обновления | Глобальный тонкий bar (`GlobalProgress`), не блокирующий скелетон поверх stale |
| Cold start (нет кэша) | Skeleton / spinner как раньше — только когда `data === undefined` |
| Сброс кэша | `resourceStore.clearAll()` на login/logout (нет утечки между аккаунтами) |
| Backend Tracker | Facade `tracker_cache` над `tracker_client` (TTL + single-flight) |
| Ключ кэша Tracker | Без OAuth-токена (один platform token) |
| Инвалидация после write | `invalidate_issue(key)` + clear list caches после comment/assign/… |
| Now-report metrics | Уже есть отдельный in-process `_METRICS_CACHE` в `tracker_metrics` (TTL ~60s) — не дублировать логикой SWR на клиенте |
| Библиотеки | Не тянуть `@tanstack/query` в этом проходе |

### TTL (backend `tracker_cache`)

| Cache | TTL |
|-------|-----|
| search issues (lists) | 20s |
| single issue | 30s |
| comments | 15s |
| transitions | 60s |
| park blockers | 20s |
| robot tickets | 30s |

Клиентский localStorage **не** имеет жёсткого TTL: stale допустим до ответа API; freshness обеспечивает revalidate-on-mount.

---

## Architecture

```
Browser
  resourceStore (mem + localStorage)
    useCachedResource(key, loader)
      → paint cached immediately
      → loader() → API
      → set(key, fresh) → re-render
  GlobalProgress ← inFlight counter

API
  routers (dashboard, blockers, tasks, tracker_*, robots, …)
    → tracker_cache.*  (ResponseCache TTL + single-flight)
      → tracker_client.*  (Startrek)

  tracker_actions (writes)
    → tracker_client mutate
    → tracker_cache.invalidate_issue(key)

  admin_settings (token rotate)
    → tracker_cache.clear_all()
```

### Components

| Piece | Responsibility |
|-------|----------------|
| `apps/web/src/lib/resource.ts` | Store, `useCachedResource`, `useIsRevalidating`, invalidate/clear |
| `apps/web/src/components/GlobalProgress.tsx` | Top progress bar while any resource revalidates |
| `apps/web/src/auth.tsx` | clear store on login/logout |
| `apps/api/.../response_cache.py` | Generic TTL + single-flight |
| `apps/api/.../tracker_cache.py` | Tracker-specific wrappers + invalidation |
| Router swaps | `tracker_client` read → `tracker_cache` where applicable |

---

## Done (уже в рабочей копии)

### Backend

- [x] `ResponseCache` + unit tests (`test_response_cache.py`)
- [x] `tracker_cache` facade + integration tests (`test_tracker_cache.py`)
- [x] Подключено чтение через cache:
  - `tracker_read` (issues / issue / comments / transitions)
  - `dashboard` (park blockers for summary)
  - `operator_blockers`, `mechanic_tasks`
  - `operator_robots`, `mechanic_robots`
  - `emergency_scope` (robot tickets for VIN ACL)
- [x] Write-path invalidation в `tracker_actions`
- [x] `clear_all` при смене Tracker token в `admin_settings`
- [x] `conftest` чистит cache между тестами
- [x] Существующий `tracker_metrics` now-report cache (отдельный, до этого эпика)

### Frontend

- [x] `resourceStore` + `useCachedResource` (mem + localStorage, persist opt-out)
- [x] `GlobalProgress` + CSS + монтирование в `main.tsx`
- [x] Clear cache on login/logout
- [x] Экраны на SWR:
  - Dashboard (`summary`, `history`)
  - MechanicTasks
  - OperatorBlockers (+ parks list)
  - RobotSearch
  - Reports (mine / inbox / detail + invalidate after mutations)
  - TrackerWorkspace (list / detail / comments / transitions + merge pagination into store)
  - IssueDrawer (detail / comments / transitions)

### Demo / ops (смежный контекст сессии, не часть SWR-кода)

- [x] `scripts/dev-demo.sh` — локальный стенд без Tuna, с чистым network egress
- [x] Docs: `DEV-ACCOUNTS.md`, README fragment
- [x] Restore Emergency VIN scope / secret migrate / CORS (отдельный PR)

---

## Todo / remaining

### Must (закрыть «во всех разделах» для Tracker UX)

- [x] **`OperatorNowReport`** — parks + report на `useCachedResource`; stale не стирается. Ключи `operator:parks`, `now-report:all` / `now-report:park:{id}`.
- [x] **Проверить cold vs warm UX** вручную на демо: аналитика, парки, блокеры (14 тикетов), drawer повторно без «Загрузка…». Logout чистит `robopark:res:*`.
- [x] **После мутаций Tracker** — Drawer/Workspace вызывают `refresh()` list+detail после assign/comment/close/transition (живой comment в Startrek не слали).
- [x] **Unit/smoke на web:** `apps/web/src/lib/resource.test.ts` (set/get/invalidate/clearAll).

### Should (смежные медленные экраны, тот же паттерн)

- [x] **EmergencyViewer** — snapshot/sections через `useCachedResource` (`persist: false`); poll 2.5s с `trackProgress: false`; staleHint при ошибке refresh.
- [x] **OperatorParks** — parks / available / requests на SWR.
- [x] **Admin** / **AdminEmergencyConfig** — `admin:bootstrap` и `admin:emergency-sections`; skeleton только на cold. AppShell badge тоже на SWR (`persist: false`).

### Could / later

- [x] Soft TTL на клиенте — 12 часов (`LS_MAX_AGE_MS`); старше → cold load.
- [ ] HTTP cache headers / `ETag` с API — не нужно при in-process + client store.
- [ ] Вынести now-report metrics cache на общий `ResponseCache` — косметика.
- [ ] Prefetch соседних экранов при hover в сайдбаре — отдельный UX-проход.

### Out of scope (явно не делать в этом эпике)

- [ ] Tuna
- [ ] Замена на React Query
- [ ] Persist tracker payloads в SQLite для UI

---

## Screen coverage matrix

| Screen / surface | Tracker-bound? | Client SWR | API tracker_cache | Notes |
|------------------|----------------|------------|-------------------|-------|
| Dashboard | yes | done | done | summary + history |
| MechanicTasks | yes | done | done | |
| OperatorBlockers | yes | done | done | |
| RobotSearch | yes | done | done | |
| TrackerWorkspace | yes | done | done | |
| IssueDrawer | yes | done | done | |
| Reports | no (DB) | done | n/a | same UX pattern |
| OperatorNowReport | yes | **done** | metrics cache | keys `now-report:all` / `now-report:park:{id}` |
| EmergencyViewer | emergency | **done** | emergency_cache exists | persist: false; poll без global bar |
| OperatorParks | no | **done** | n/a | |
| Admin* | no | **done** | token clear_all done | `admin:bootstrap`, `admin:emergency-sections` |
| AppShell badge | light | **done** | n/a | `reports:badge:{role}:{park\|all}`, persist false |

---

## UX rules (acceptance)

1. **Warm mount:** при наличии кэша по ключу пользователь видит контент **до** завершения HTTP; нет полноэкранного пустого состояния.
2. **Revalidate signal:** пока идёт фоновый fetch, виден `GlobalProgress` (или эквивалент), контент кликабелен.
3. **Cold mount:** без кэша — skeleton/spinner; после успеха — данные + запись в store.
4. **Error with stale:** ошибка сети **не** стирает уже показанный stale; error alert рядом / сверху (как сейчас принято на экране). Сейчас в `useCachedResource` ошибка ставится, data остаётся — сохранить это поведение на всех миграциях.
5. **Logout/login:** чужой кэш не показывается.
6. **Write then list:** после успешного Tracker write list/detail не показывают заведомо устаревшее дольше одного revalidate (invalidate + refresh).

---

## Open questions (для следующего шага с пользователем)

1. **OperatorNowReport:** при смене фильтра парка ключ кэша `now-report:all` vs `now-report:park:{id}` — ок? (предложение: да.)
2. **Emergency:** кэшировать snapshot по VIN в `useCachedResource` с `persist: true` или только mem (`persist: false`), т.к. телеметрия чувствительнее тикетов?
3. **Визуальный маркер «данные устарели»** (мелкий «обновлено HH:MM» / dim overlay) — нужен сейчас или достаточно progress bar?
4. **TTL backend:** 15–60s достаточно, или на демо хочется агрессивнее (например lists 5s)?
5. Коммитить SWR-эпик **одним PR** или дробить backend cache / frontend resource / screen migrations?

---

## Implementation plan (остаток)

Полный пошаговый план: [`docs/superpowers/plans/2026-08-26-robopark-stale-while-revalidate.md`](../plans/2026-08-26-robopark-stale-while-revalidate.md).

Кратко: код эпика готов. Осталось по запросу пользователя — коммит/PR и Could (soft TTL, ETag, prefetch).

---

## Test plan

### API

- Уже: `test_response_cache.py`, `test_tracker_cache.py`.
- Регрессия: существующие router/emergency/mechanic тесты с `clear_all` в conftest.

### Web (ручной / автоматический)

- Cold: очистить localStorage `robopark:res:*` → открыть Dashboard → skeleton → данные.
- Warm: уйти и вернуться → мгновенный paint + progress → без мигания в пустоту.
- Logout → login другим пользователем → нет чужих KPI/тикетов.
- Mutation: комментарий в IssueDrawer → list/detail обновляются.
- Now-report после фикса: смена парка не стирает предыдущий снимок до прихода нового ключа; для нового ключа — cold или свой cache.

---

## Handoff — с чего продолжать (нулевой контекст)

Код **уже в рабочей копии**, не закоммичен. Не начинать с нуля: добить Must → ручной чеклист → Should → коммит/PR.

```bash
# Демо без Tuna (API + web, без sandbox-proxy)
scripts/dev-demo.sh run-api   # отдельный терминал, нужен full network
scripts/dev-demo.sh run-web   # отдельный терминал

# Аккаунты: docs/DEV-ACCOUNTS.md  (operator / mechanic / admin)
```

**Первый код-таск:** эпик SWR закрыт. Дальше: коммит/PR по запросу или следующий пункт фидбека.

Детальные шаги: план выше.

---

## Inventory — весь кэш (файлы в рабочей копии)

### Новые (untracked)

| Path | Role |
|------|------|
| `apps/api/src/robopark_api/services/response_cache.py` | Generic TTL + single-flight |
| `apps/api/src/robopark_api/services/tracker_cache.py` | Facade над `tracker_client` |
| `apps/api/tests/test_response_cache.py` | Unit tests ResponseCache |
| `apps/api/tests/test_tracker_cache.py` | Facade + invalidate tests |
| `apps/web/src/lib/resource.ts` | Client SWR store + hook |
| `apps/web/src/components/GlobalProgress.tsx` | Top bar while revalidating |
| `docs/superpowers/specs/2026-08-26-robopark-stale-while-revalidate-design.md` | Эта спека |
| `docs/superpowers/plans/2026-08-26-robopark-stale-while-revalidate.md` | План добивки |

### API routers / services (modified → `tracker_cache`)

| Path | Change |
|------|--------|
| `routers/tracker_read.py` | reads via cache |
| `routers/tracker_actions.py` | writes + `invalidate_issue` |
| `routers/dashboard.py` | `fetch_park_blockers` via cache |
| `routers/operator_blockers.py` | same |
| `routers/mechanic_tasks.py` | same |
| `routers/operator_robots.py` | `search_robot_tickets` via cache |
| `routers/mechanic_robots.py` | same |
| `routers/admin_settings.py` | `tracker_cache.clear_all()` on token change |
| `services/emergency_scope.py` | ACL VIN tickets via cache |
| `tests/conftest.py` | `clear_all()` between tests |

### Web screens (modified → `useCachedResource`)

| Path | Status |
|------|--------|
| `pages/Dashboard.tsx` | done |
| `pages/MechanicTasks.tsx` | done |
| `pages/OperatorBlockers.tsx` | done |
| `pages/RobotSearch.tsx` | done |
| `pages/Reports.tsx` | done |
| `components/tracker/TrackerWorkspace.tsx` | done |
| `components/tracker/IssueDrawer.tsx` | done |
| `pages/OperatorNowReport.tsx` | **done** |
| `pages/OperatorParks.tsx` | **done** |
| `pages/Admin.tsx` | **done** (`admin:bootstrap`) |
| `pages/AdminEmergencyConfig.tsx` | **done** |
| `components/emergency/EmergencyViewer.tsx` | **done** |
| `components/AppShell.tsx` | **done** (badge) |
| `auth.tsx` | clearAll on login/logout |
| `main.tsx` | mounts `<GlobalProgress />` |
| `index.css` | `.global-progress` styles |

### Смежное в той же ветке (не SWR, но лежит рядом — не терять)

| Path | Note |
|------|------|
| `scripts/dev-demo.sh` | демо без Tuna |
| `README.md`, `docs/DEV-ACCOUNTS.md` | упоминание скрипта |
| `docs/UI-REFACTOR-SPEC.md` | deleted (legacy) |
| `apps/api/logs/` | **не коммитить** |

---

## Client cache keys (уже в коде)

| Key pattern | Screen |
|-------------|--------|
| `dashboard:summary:{parkId}` | Dashboard |
| `dashboard:history:{parkId}:7` | Dashboard |
| `mechanic:tasks:{status}` | MechanicTasks |
| `mechanic:reports:mine` | MechanicTasks |
| `operator:parks` | OperatorBlockers |
| `operator:blockers:{parkId}:{status}` | OperatorBlockers |
| `robot:tickets:{role}:{query}` | RobotSearch |
| `reports:mine` / `reports:inbox:{parkId}` / `reports:detail:{id}` | Reports |
| `tracker:list:{JSON.stringify(filters)}` | TrackerWorkspace |
| `tracker:issue:{key}` | Workspace + IssueDrawer |
| `tracker:comments:{key}` | same |
| `tracker:transitions:{key}` | same |

**Proposed (ещё не в коде):**

| Key | Screen |
|-----|--------|
| `operator:parks` (reuse) + `now-report:{parkId\|all}` | OperatorNowReport **done** |
| `emergency:snapshot:{vin}` / `emergency:section:{vin}:{id}` / `emergency:resolve:{query}` | EmergencyViewer **done** (mem only) |
| `operator:parks` / `operator:available-parks` / `operator:park-requests` | OperatorParks **done** |

localStorage prefix: `robopark:res:` + key. Version field `v: 1` inside JSON entry.

---

## Backend cache API (уже в коде)

```text
tracker_cache.search_issues / get_issue / list_comments / list_transitions
tracker_cache.fetch_park_blockers / search_robot_tickets
tracker_cache.invalidate_issue(key)   # issue+comments+transitions + clear lists
tracker_cache.invalidate_all_lists()
tracker_cache.clear_all()             # token rotate / tests
```

TTL: issues 20s, issue 30s, comments 15s, transitions 60s, blockers 20s, robot_tickets 30s.

Отдельно (до эпика): `tracker_metrics.get_cached_now_report` / `set_cached_now_report` (~60s) для `/operator/now-report`.

Emergency: `emergency_cache` на API + клиентский SWR viewer (`persist: false`).

---

## Refs

- Plan: [`2026-08-26-robopark-stale-while-revalidate.md`](../plans/2026-08-26-robopark-stale-while-revalidate.md)
- Code: `apps/web/src/lib/resource.ts`, `GlobalProgress.tsx`
- Code: `apps/api/src/robopark_api/services/response_cache.py`, `tracker_cache.py`
- Demo: `scripts/dev-demo.sh`, `docs/DEV-ACCOUNTS.md`
- Prior: phase4 now-report metrics cache in `tracker_metrics.py`
- Transcript: [ревью и демо](6f10dd01-f732-415c-b5ef-2edde532069d)
