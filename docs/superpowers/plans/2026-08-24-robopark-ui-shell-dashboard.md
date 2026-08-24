# Robopark UI Shell + Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Полный редизайн web UI (общий shell, RU, light/dark) + дашборд парка на Tracker с историей приходов/уходов блокеров за 7 дней (job каждые 2 часа); заглушки и каркас репортов/аналитики; next-pass backlog не реализовывать.

**Architecture:** Один `AppShell` с role-filtered сайдбаром; CSS tokens через `data-theme`; legacy URL → redirects; history в таблице `park_blocker_history` per-park, фоновый scan Tracker → DB; UI читает summary + history API.

**Tech Stack:** React 19 + Vite + react-router-dom 7, plain CSS; FastAPI + SQLAlchemy + Alembic + pytest; Tracker client (existing).

**Base branch:** `main` **после merge Phase 6** (Emergency DB). If Phase 6 not merged yet, branch from `feature/phase6-emergency-db-config` and rebase onto `main` after merge. Work in a dedicated worktree: `feature/ui-shell-dashboard`.

**Spec:** `docs/superpowers/specs/2026-08-24-robopark-ui-shell-dashboard-design.md`

## Global Constraints

- UI language: Russian only; brand string **«Робопарк Сервис»**
- Theme: light (default) + dark; persist `localStorage` key `robopark-theme`; apply `data-theme="light|dark"` on `document.documentElement`
- Light palette: bg ≈ `#F5F5F5`, cards white, accent orange ≈ `#F15A24`, status green for OK
- Dark: charcoal surfaces, same orange accent, same radii/layout
- Nav: single shell; stubs visible as «Скоро»; Analytics/Reports for **all** roles (role-specific copy); full reports workflow = **out of scope**
- Chart: arrivals + departures of blockers, **7 days**, history refreshed every **2 hours** via hybrid job (scan Tracker → save per park)
- Park filter: `tracker_queue` + `tag`; optional `tracker_priority` (default `blocker`), `tracker_type` (empty = omit)
- No browser polling for Emergency; dashboard may refresh on mount / manual button only (no `setInterval` charts)
- Do not implement Next pass backlog items from the spec

---

## File structure (target)

### Web

| File | Responsibility |
|------|----------------|
| `apps/web/src/theme.ts` | get/set theme, apply to DOM |
| `apps/web/src/nav.ts` | NavItem defs + `navItemsForRole(role)` |
| `apps/web/src/park-context.tsx` | Selected park id for header switcher |
| `apps/web/src/components/AppShell.tsx` | Sidebar + header (Парк, Пользователь, theme) |
| `apps/web/src/components/PageShell.tsx` | Slim down to content chrome inside AppShell or deprecate |
| `apps/web/src/components/StubSoon.tsx` | Shared «Скоро» panel |
| `apps/web/src/pages/Dashboard.tsx` | Widgets + chart |
| `apps/web/src/pages/ComingSoon.tsx` | Map / Learning / Help |
| `apps/web/src/pages/Analytics.tsx` | Role-aware каркас |
| `apps/web/src/pages/Reports.tsx` | Role-aware каркас + contract note |
| `apps/web/src/pages/Tasks.tsx` | Role switch → existing task/blocker UIs |
| `apps/web/src/index.css` | Tokens + shell + restyle |
| `apps/web/src/App.tsx` | New routes + redirects |
| `apps/web/src/i18n/ru.ts` | Shell/nav/dashboard strings |
| `apps/web/src/api.ts` | `dashboardSummary`, `dashboardHistory` |

### API

| File | Responsibility |
|------|----------------|
| `apps/api/alembic/versions/0005_ui_dashboard_history.py` | Park columns + history table |
| `apps/api/src/robopark_api/models.py` | `Park.tracker_priority/type`, `ParkBlockerHistory` |
| `apps/api/src/robopark_api/services/blocker_history.py` | Upsert bucket, read series, scan-once |
| `apps/api/src/robopark_api/services/blocker_history_job.py` | Async loop every 2h |
| `apps/api/src/robopark_api/routers/dashboard.py` | `GET /dashboard/summary`, `GET /dashboard/history` |
| `apps/api/src/robopark_api/schemas.py` | Dashboard DTOs |
| `apps/api/src/robopark_api/main.py` | Register router + start history job |
| `apps/api/tests/test_blocker_history.py` | Upsert / retention / authz helpers |
| `apps/api/tests/test_dashboard_router.py` | API tests |

---

### Task 1: Theme tokens + theme helper

**Files:**
- Create: `apps/web/src/theme.ts`
- Modify: `apps/web/src/index.css` (replace `:root` blue theme with light/dark tokens)
- Modify: `apps/web/src/main.tsx` (call `applyStoredTheme()` before render)
- Modify: `apps/web/src/i18n/ru.ts` (theme labels)

**Interfaces:**
- Produces: `export type Theme = 'light' | 'dark'`; `getStoredTheme(): Theme`; `setTheme(theme: Theme): void`; `applyStoredTheme(): void`; `THEME_STORAGE_KEY = 'robopark-theme'`

- [ ] **Step 1: Add theme helper**

```ts
// apps/web/src/theme.ts
export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'robopark-theme'

export function getStoredTheme(): Theme {
  const raw = localStorage.getItem(THEME_STORAGE_KEY)
  return raw === 'dark' ? 'dark' : 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(THEME_STORAGE_KEY, theme)
  applyTheme(theme)
}

export function applyStoredTheme(): void {
  applyTheme(getStoredTheme())
}
```

- [ ] **Step 2: Replace CSS tokens**

In `index.css`, keep Manrope import. Replace `:root` block with light defaults and `[data-theme='dark']` overrides. Exact tokens:

```css
:root,
[data-theme='light'] {
  color: #1a1a1a;
  background: #f5f5f5;
  font-family: Manrope, system-ui, sans-serif;
  --bg: #f5f5f5;
  --surface: #ffffff;
  --surface-muted: #eeeeee;
  --border: #e0e0e0;
  --text: #1a1a1a;
  --text-muted: #6b6b6b;
  --brand: #f15a24;
  --brand-strong: #d94a1a;
  --accent: #f15a24;
  --success: #22c55e;
  --danger: #dc2626;
  --shadow: 0 8px 24px rgb(0 0 0 / 6%);
  --radius: 1rem;
  --radius-pill: 999px;
  --sidebar-width: 15rem;
}

[data-theme='dark'] {
  color: #f5f5f5;
  background: #121212;
  --bg: #121212;
  --surface: #1e1e1e;
  --surface-muted: #2a2a2a;
  --border: #333333;
  --text: #f5f5f5;
  --text-muted: #a3a3a3;
  --brand: #f15a24;
  --brand-strong: #ff6a35;
  --accent: #f15a24;
  --success: #4ade80;
  --danger: #f87171;
  --shadow: 0 8px 24px rgb(0 0 0 / 40%);
}
```

Update `body { background: var(--bg); color: var(--text); }`. Do **not** restyle all components yet — enough that app is not broken (temporary map old `--brand` usages).

- [ ] **Step 3: Boot theme in main.tsx**

```ts
import { applyStoredTheme } from './theme'
applyStoredTheme()
```

- [ ] **Step 4: Verify build**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/theme.ts apps/web/src/index.css apps/web/src/main.tsx apps/web/src/i18n/ru.ts
git commit -m "feat(web): light/dark theme tokens and persistence"
```

---

### Task 2: Nav config (role-filtered)

**Files:**
- Create: `apps/web/src/nav.ts`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**
- Produces:
```ts
export type NavId =
  | 'dashboard' | 'tasks' | 'robot_search' | 'emergency'
  | 'map' | 'analytics' | 'reports' | 'learning' | 'help'

export type NavItem = {
  id: NavId
  path: string
  label: string
  stub?: boolean
}

export function navItemsForRole(role: string): NavItem[]
```

- [ ] **Step 1: Implement nav.ts**

All roles see the full sketch list. Labels from `ru.nav.*`. Paths:

| id | path | stub |
|----|------|------|
| dashboard | `/dashboard` | false |
| tasks | `/tasks` | false |
| robot_search | `/robots/search` | false |
| emergency | `/emergency` | false |
| map | `/map` | true |
| analytics | `/analytics` | false (каркас) |
| reports | `/reports` | false (каркас) |
| learning | `/learning` | true |
| help | `/help` | true |

`navItemsForRole`: return the same ordered list for `mechanic` | `operator` | `admin` | `royal`. (Filtering of *data* happens on pages; menu visibility is full per spec «для всех» on analytics/reports and stubs.)

- [ ] **Step 2: Add ru.nav strings**

```ts
nav: {
  dashboard: 'Дашборд',
  tasks: 'Задачи',
  robot_search: 'Поиск по роботу',
  emergency: 'Проверка по роботу',
  map: 'Карта',
  analytics: 'Аналитика',
  reports: 'Репорты',
  learning: 'Обучение',
  help: 'Помощь',
  soon: 'Скоро',
  park: 'Парк',
  user: 'Пользователь',
  themeLight: 'Светлая тема',
  themeDark: 'Тёмная тема',
  admin: 'Администрирование',
  brand: 'Робопарк Сервис',
},
```

Update top-level `brand` to `'Робопарк Сервис'` if different.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/nav.ts apps/web/src/i18n/ru.ts
git commit -m "feat(web): role sidebar nav config in Russian"
```

---

### Task 3: AppShell + park context

**Files:**
- Create: `apps/web/src/components/AppShell.tsx`
- Create: `apps/web/src/park-context.tsx`
- Modify: `apps/web/src/index.css` (`.app-shell`, `.sidebar`, `.topbar`, active nav)
- Modify: `apps/web/src/auth.tsx` or consumers — park list from `user.parks` / API

**Interfaces:**
- Consumes: `navItemsForRole`, `setTheme`/`getStoredTheme`, `useAuth`
- Produces: `<AppShell children />`; `useParkContext(): { parkId: number | null; setParkId; parks }`

- [ ] **Step 1: Park context**

```tsx
// park-context.tsx — selected park id in React state + sessionStorage key robopark-park-id
// parks from auth user (operator/mechanic assignments) or fetched admin parks list
```

Rules:
- mechanic: force single park; hide switcher or disable
- operator: select among assigned parks
- admin/royal: select any active park (load `GET /parks` or admin parks endpoint already used in Admin.tsx)

- [ ] **Step 2: AppShell layout**

Structure:

```tsx
<div className="app-shell">
  <aside className="sidebar">
    <div className="sidebar-brand">{ru.nav.brand}</div>
    <nav>
      {items.map(item => (
        <NavLink key={item.id} to={item.path} className={({isActive}) => isActive ? 'nav-item active' : 'nav-item'}>
          {item.label}
          {item.stub ? <span className="nav-soon">{ru.nav.soon}</span> : null}
        </NavLink>
      ))}
    </nav>
  </aside>
  <div className="app-main">
    <header className="topbar">
      {/* Парк select */}
      {/* Пользователь menu: theme toggle, admin link, logout */}
    </header>
    <div className="app-content">{children}</div>
  </div>
</div>
```

Login/Register/NoCabinet: **do not** wrap in AppShell.

- [ ] **Step 3: CSS for shell**

Sidebar white/surface, active item `surface-muted` rounded square, topbar with pill controls, orange brand mark optional 2×2 block.

- [ ] **Step 4: Build**

Run: `cd apps/web && npm run build`  
Expected: success (AppShell may be unused until Task 4)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/AppShell.tsx apps/web/src/park-context.tsx apps/web/src/index.css
git commit -m "feat(web): AppShell sidebar and park context"
```

---

### Task 4: Routes, redirects, wrap authenticated pages

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/routes.ts` (`pathForUser` → `/dashboard`)
- Create: `apps/web/src/pages/ComingSoon.tsx`
- Create: `apps/web/src/pages/Analytics.tsx`
- Create: `apps/web/src/pages/Reports.tsx`
- Create: `apps/web/src/pages/Tasks.tsx` (compose existing mechanic/operator UIs)
- Create thin wrappers or reuse: search, emergency under new paths

**Interfaces:**
- New public paths from nav.ts
- Redirects: `/operator` → `/dashboard`, `/operator/blockers` → `/tasks`, `/mechanic/tasks` → `/tasks`, `/operator/robot-search` & `/mechanic/robot-search` → `/robots/search`, `/operator/emergency` & `/mechanic/emergency` & `/admin/emergency` → `/emergency`, `/operator/now-report` → `/analytics`, `/learning|/map|/help` → ComingSoon

- [ ] **Step 1: ComingSoon / Analytics / Reports pages**

```tsx
// ComingSoon.tsx — title from route; Panel with ru.nav.soon
// Analytics.tsx — role copy:
//   mechanic: «Аналитика вашего парка (каркас)»
//   operator: «Аналитика парков + репорты механиков (каркас)»
//   admin: «Сводка по паркам (каркас)»
// Reports.tsx — role copy for chain mechanic→operator→admin; note «Полный workflow — следующий проход»
```

- [ ] **Step 2: Tasks / search / emergency unified routes**

- `/tasks`: if mechanic render MechanicTasks content; if operator OperatorBlockers; if admin link/embed AdminTrackerWorkspace or list message + link to `/admin` tools
- `/robots/search`: shared search component (extract from OperatorRobotSearch / MechanicRobotSearch if duplicated)
- `/emergency`: `<EmergencyViewer />` without old backTo hubs (use shell)

- [ ] **Step 3: Wire App.tsx**

Authenticated tree:

```tsx
<Route element={<RequireAuth><ParkProvider><AppShell /></ParkProvider></RequireAuth>}>
  <Route path="/dashboard" element={<DashboardPlaceholder />} /> {/* Task 11 fills */}
  <Route path="/tasks" element={<Tasks />} />
  ...
</Route>
```

Until Task 11, Dashboard can show EmptyState «Дашборд подключается» or reuse now-report summary read-only.

- [ ] **Step 4: pathForUser**

Approved mechanic/operator/admin → `/dashboard`. Keep pending/rejected/no-park paths.

- [ ] **Step 5: Build + smoke**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/routes.ts apps/web/src/pages/
git commit -m "feat(web): unified routes, stubs, and legacy redirects"
```

---

### Task 5: Restyle existing screens to new tokens

**Files:**
- Modify: `apps/web/src/index.css` (buttons, panels, forms, login-card, badges — map to new tokens; remove blue gradient header styles)
- Modify: pages that hardcode layout assuming old PageShell header (Admin, Login, Register, tracker pages)
- Modify: `PageShell.tsx` — if still used inside content, make it a simple title row without old gradient header

**Interfaces:**
- Consumes: CSS variables from Task 1
- Visual target: screenshot soft UI (pill inputs, orange primary buttons, white cards)

- [ ] **Step 1: Restyle primitives in CSS**

Update `.btn`, `.btn-primary` (orange solid), `.btn-ghost`, `.panel`, `input`/`select` (pill border), `.login-card`, `.badge-ok` (green). Remove dark-blue `.shell-header` gradient; content titles use plain text.

- [ ] **Step 2: Fix pages broken by shell change**

Walk: Login, Register, Admin, OperatorBlockers, MechanicTasks, EmergencyViewer, tracker workspaces — ensure no double headers; use `h1`/`Panel` inside `app-content`.

- [ ] **Step 3: Build**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/index.css apps/web/src/components/PageShell.tsx apps/web/src/pages apps/web/src/components
git commit -m "style(web): restyle screens to orange soft UI tokens"
```

---

### Task 6: Park tracker_priority / tracker_type + migration

**Files:**
- Create: `apps/api/alembic/versions/0005_ui_dashboard_history.py` (start with park columns only; Task 7 adds history table in same revision **or** split 0005 parks / 0006 history — prefer **one revision 0005** with both for fewer migrate steps)
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: admin park create/update schemas + Admin UI fields
- Test: `apps/api/tests/test_models_migration.py` (head revision includes new columns)

**Interfaces:**
- Produces: `Park.tracker_priority: str | None` default `"blocker"` at app layer; `Park.tracker_type: str | None`
- QL helpers must accept priority/type (Task 7/8)

- [ ] **Step 1: Failing migration/model test expectation**

Extend migration test to assert columns exist after upgrade (pattern from existing `test_models_migration.py`).

- [ ] **Step 2: Alembic 0005**

```python
op.add_column('parks', sa.Column('tracker_priority', sa.String(length=64), nullable=True))
op.add_column('parks', sa.Column('tracker_type', sa.String(length=64), nullable=True))
# + park_blocker_history table in Task 7 same file if combining
```

- [ ] **Step 3: Model + admin API/UI fields**

Default when null in query builders: priority `"blocker"`; type omitted.

- [ ] **Step 4: pytest**

Run: `cd apps/api && python -m pytest tests/test_models_migration.py -q`  
Expected: pass

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0005_ui_dashboard_history.py apps/api/src/robopark_api/models.py apps/api/src/robopark_api/schemas.py apps/api/tests apps/web/src/pages/Admin.tsx
git commit -m "feat(api): optional park tracker priority and type"
```

---

### Task 7: park_blocker_history model + service upsert/read

**Files:**
- Modify: `0005_ui_dashboard_history.py` (table)
- Modify: `models.py` — `ParkBlockerHistory`
- Create: `apps/api/src/robopark_api/services/blocker_history.py`
- Create: `apps/api/tests/test_blocker_history.py`

**Interfaces:**
```python
def upsert_bucket(
    db: Session,
    *,
    park_id: int,
    bucket_start: datetime,
    arrived_count: int,
    departed_count: int,
    scanned_at: datetime | None = None,
) -> ParkBlockerHistory: ...

def history_series(
    db: Session,
    *,
    park_id: int,
    days: int = 7,
) -> list[dict]:  # [{bucket_start, arrived_count, departed_count}, ...]
```

Unique `(park_id, bucket_start)`. Retention helper: delete buckets older than `retention_days` (default 30).

- [ ] **Step 1: Write failing tests**

```python
def test_upsert_bucket_idempotent(db_session, seed_park):
    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    upsert_bucket(db_session, park_id=seed_park.id, bucket_start=t0, arrived_count=2, departed_count=1)
    upsert_bucket(db_session, park_id=seed_park.id, bucket_start=t0, arrived_count=5, departed_count=3)
    rows = history_series(db_session, park_id=seed_park.id, days=7)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 5
    assert rows[0]["departed_count"] == 3
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_blocker_history.py::test_upsert_bucket_idempotent -v`  
Expected: FAIL (import/table missing)

- [ ] **Step 3: Implement model + upsert/read**

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): park blocker history upsert and series read"
```

---

### Task 8: History scan once + 2h job

**Files:**
- Modify: `blocker_history.py` — `scan_park_bucket(db, park, bucket_start, bucket_end) -> tuple[int,int]`
- Create: `apps/api/src/robopark_api/services/blocker_history_job.py`
- Modify: `tracker_metrics.py` or `tracker_client.py` — queries for created/resolved in window using park queue/tag/priority/type
- Modify: `main.py` lifespan — start `run_blocker_history_loop` alongside keepalive
- Test: `test_blocker_history.py` with monkeypatched `count_issues`

**Interfaces:**
```python
BUCKET_SECONDS = 2 * 60 * 60
async def run_blocker_history_loop(stop_event: asyncio.Event) -> None: ...
def scan_all_parks_once(db: Session, *, now: datetime | None = None) -> int:  # parks scanned
```

Rules:
- Skip inactive parks or missing queue/tag
- Batch with small parallelism (sequential is OK for v1; document cap)
- On Tracker error: log, skip park, continue
- Bucket align: `bucket_start = now_utc.replace(minute=0, second=0, microsecond=0)` then subtract `now.hour % 2` hours (even-hour grid)

- [ ] **Step 1: Failing test for scan_park_bucket with mocks**

- [ ] **Step 2: Implement QL for arrived/departed in `[bucket_start, bucket_end)`**

Reuse patterns from `build_arrived_today_query` / `build_done_today_query` but with explicit Created/Resolved range for the bucket window and park priority/type.

- [ ] **Step 3: Job loop**

Mirror `emergency_keepalive.run_keepalive_loop`: sleep 2h (or sleep until next even hour), `asyncio.to_thread(scan_all_parks_once)`, respect stop_event.

- [ ] **Step 4: pytest**

Run: `python -m pytest tests/test_blocker_history.py -q`  
Expected: pass

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): blocker history scan job every 2 hours"
```

---

### Task 9: Dashboard API

**Files:**
- Create: `apps/api/src/robopark_api/routers/dashboard.py`
- Modify: `schemas.py` — `DashboardSummaryOut`, `DashboardHistoryOut`
- Modify: `main.py` — include router
- Create: `apps/api/tests/test_dashboard_router.py`
- Deps: reuse `require_user` + park access checks (operator parks / mechanic park / admin)

**Interfaces:**
- `GET /dashboard/summary?park_id=` → `{ park_id, arrived, done, queued, in_transit, moving: [{key, summary}] }`  
  Counts: reuse `tracker_metrics` park metrics; moving list: thin call to blockers/issues status moving (limit 20).
- `GET /dashboard/history?park_id=&days=7` → `{ park_id, points: [...] }` from `history_series`

Authz failures → 403; missing park → 404.

- [ ] **Step 1: Failing router tests** (operator ok, mechanic other park 403, history shape)

- [ ] **Step 2: Implement router**

- [ ] **Step 3: pytest**

Run: `python -m pytest tests/test_dashboard_router.py tests/test_blocker_history.py -q`  
Expected: pass

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(api): dashboard summary and history endpoints"
```

---

### Task 10: Dashboard UI + chart

**Files:**
- Create: `apps/web/src/pages/Dashboard.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/index.css` (dashboard grid, chart)
- No new chart library required: SVG polyline from points (two series) to avoid dependency; or minimal inline SVG

**Interfaces:**
- Consumes: `api.dashboardSummary(parkId)`, `api.dashboardHistory(parkId, 7)`, `useParkContext`

Layout matching sketch:
1. Large chart card (arrived vs departed)
2. «Перемещение» list
3. KPI block Пришли / Ушли / В очереди

Empty: no park selected; no history yet — Russian hints. Manual «Обновить» button only (no `setInterval`).

- [ ] **Step 1: API client methods**

- [ ] **Step 2: Dashboard page + SVG chart**

- [ ] **Step 3: Build**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(web): park dashboard with blocker history chart"
```

---

### Task 11: Docs + full verification

**Files:**
- Modify: root `README.md` — UI shell, theme toggle, dashboard/history job note
- Modify: spec status line if needed (`Status: implemented` only after done)

- [ ] **Step 1: README section**

Russian short: как переключить тему, сайдбар, дашборд зависит от парка и job 2ч.

- [ ] **Step 2: Full API tests**

Run: `cd apps/api && python -m pytest -q`  
Expected: all pass

- [ ] **Step 3: Web build**

Run: `cd apps/web && npm run build`  
Expected: success

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: UI shell and dashboard usage notes"
```

---

## Self-review (plan vs spec)

| Spec item | Task |
|-----------|------|
| Light/dark + localStorage | 1, 3 |
| AppShell + RU nav + stubs | 2–4 |
| Full restyle | 5 |
| Analytics/Reports каркас all roles | 4 |
| Legacy redirects | 4 |
| Park priority/type | 6 |
| History per-park + 2h job + chart 7d | 7–10 |
| Next pass backlog not built | Explicit non-goal; Reports only каркас |
| Brand «Робопарк Сервис» | 2 |

No TBD placeholders. Types aligned: `history_series` → dashboard history → web chart.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-24-robopark-ui-shell-dashboard.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session with executing-plans and checkpoints  

Which approach?
