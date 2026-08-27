# Stale-while-revalidate (Tracker UX) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добить паттерн «сразу из памяти → фоновое обновление с API» на всех Tracker-экранах и смежных медленных поверхностях; ядро уже в рабочей копии.

**Architecture:** Клиент — `resourceStore` + `useCachedResource` (mem + `localStorage` `robopark:res:*`) + `GlobalProgress`. API — `tracker_cache` (TTL + single-flight) над `tracker_client`, invalidate после writes.

**Tech Stack:** React 19 + Vite; FastAPI; существующий `tracker_client` / `emergency_cache`.

**Base branch:** текущий `main` + uncommitted SWR WIP (см. inventory в спеке). Не создавать worktree с нуля — продолжать в этой рабочей копии.

**Spec:** [`docs/superpowers/specs/2026-08-26-robopark-stale-while-revalidate-design.md`](../specs/2026-08-26-robopark-stale-while-revalidate-design.md)

## Global Constraints

- Не обнулять UI при revalidate, если есть stale (`setX(null)` перед fetch запрещён)
- Skeleton только при cold start: `data === undefined && isRevalidating`
- Ошибка сети не стирает stale; alert поверх
- `resourceStore.clearAll()` на login/logout уже есть — не ломать
- Tuna / React Query / SW — out of scope
- Не коммитить `apps/api/logs/`
- UI language: Russian only

---

## Already done (не переделывать)

См. спеку § Done + § Inventory. Кратко:

- Backend: `response_cache.py`, `tracker_cache.py`, tests, routers wired, write invalidate, admin token clear
- Frontend: `lib/resource.ts`, `GlobalProgress`, Dashboard / MechanicTasks / OperatorBlockers / RobotSearch / Reports / TrackerWorkspace / IssueDrawer
- Demo: `scripts/dev-demo.sh`

---

## File structure (остаток)

| File | Responsibility |
|------|----------------|
| `apps/web/src/pages/OperatorNowReport.tsx` | **Must** — SWR parks + report |
| `apps/web/src/components/emergency/EmergencyViewer.tsx` | Should — SWR snapshot/sections |
| `apps/web/src/pages/OperatorParks.tsx` | Should — SWR lists |
| `apps/web/src/pages/Admin.tsx` | Could — optional |
| `apps/web/src/pages/AdminEmergencyConfig.tsx` | Could — optional |
| Spec status → `implemented` когда Must закрыты | |

---

### Task 1: Починить OperatorNowReport (Must)

**Files:**
- Modify: `apps/web/src/pages/OperatorNowReport.tsx`

**Проблема сейчас:**
- `import { useCachedResource }` есть, но экран на ручном state
- `useEffect` / `useCallback` / `useRef` используются без импорта (сборка может падать)
- `setReport(null)` перед fetch → мигание в пустоту (антипаттерн к спеке)

**Keys:**
- Parks: `operator:parks` (тот же ключ, что в OperatorBlockers — shared)
- Report: `now-report:all` или `now-report:park:{id}`

- [ ] **Step 1: Переписать загрузку на хук**

Убрать локальные `report`/`loading`/`parksLoading`/`requestIdRef`/`loadReport` useEffect. Пример целевого каркаса:

```tsx
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type NowReport, type Park } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonKpi, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'

// … TOTAL_KEYS / MetricsGrid helpers без изменений …

export function OperatorNowReport() {
  const [parkFilter, setParkFilter] = useState<number | null>(null)

  const parksRes = useCachedResource<Park[]>('operator:parks', () => api.operatorParks())
  const parks = parksRes.data ?? []

  const reportKey =
    parksRes.data === undefined ? '' : parkFilter == null ? 'now-report:all' : `now-report:park:${parkFilter}`
  const reportRes = useCachedResource<NowReport>(
    reportKey,
    () => api.operatorNowReport(parkFilter ?? undefined),
    { enabled: parksRes.data !== undefined && parks.length > 0 },
  )

  const report = reportRes.data
  const loading = reportRes.isLoading
  const parksLoading = parksRes.isLoading
  const error =
    (parksRes.error && mapApiError(parksRes.error, ru.errors.load)) ||
    (reportRes.error && mapApiError(reportRes.error, ru.errors.load)) ||
    ''

  // В кнопке «Обновить»: onClick={() => void reportRes.refresh()}
  // KPI/списки: показывать report сразу если есть; skeleton только если loading && !report
  // …
}
```

- [ ] **Step 2: Поправить условия рендера**

- Итого / По паркам: `{loading && !report && <Skeleton…/>}` + `{report && <MetricsGrid…/>}`
- Не прятать stale за `!loading && report`
- Кнопка disabled только при `reportRes.isRevalidating` (или parks empty), не при наличии stale

- [ ] **Step 3: Проверка в браузере**

```bash
# demo accounts: operator / RoboparkOperator!1
# 1) Открыть «Сейчас по Tracker» — cold skeleton ok
# 2) Уйти на Dashboard и вернуться — мгновенный paint + progress bar
# 3) Сменить парк — другой ключ; cold или свой cache; старый ключ не стирается
```

- [ ] **Step 4: Commit** (только когда пользователь попросит commit; иначе оставить в WIP)

```bash
git add apps/web/src/pages/OperatorNowReport.tsx
# message: fix(web): SWR for OperatorNowReport — keep stale while revalidating
```

---

### Task 2: Ручной чеклист Must-экранов

**Files:** none (QA)

- [ ] **Step 1: Cold/warm на каждом done-экране**

| Screen | Account | Cold | Warm |
|--------|---------|------|------|
| Dashboard | operator/admin | skeleton once | instant + bar |
| MechanicTasks | mechanic | ok | ok |
| OperatorBlockers | operator | ok | ok |
| RobotSearch | operator | после submit | повторный query instant |
| Tracker workspace | admin | ok | ok |
| IssueDrawer | mechanic | ok | ok |
| Reports | mechanic/admin | ok | ok |
| NowReport (после Task 1) | operator | ok | ok |

- [ ] **Step 2: Logout → login другим юзером** — нет чужих KPI/тикетов в localStorage paint

- [ ] **Step 3: Mutation** — comment/assign в Drawer → list/detail обновляются (refresh после write)

---

### Task 3: EmergencyViewer SWR (Should)

**Files:**
- Modify: `apps/web/src/components/emergency/EmergencyViewer.tsx`

**Open question from spec:** `persist: true` vs `false` для snapshot. **Default until answered:** `persist: false` (только mem — телеметрия чувствительнее тикетов).

- [ ] **Step 1: Snapshot по VIN**

```tsx
const snapshotRes = useCachedResource(
  vin ? `emergency:snapshot:${vin}` : '',
  () => api.emergencySnapshot(vin),
  { enabled: Boolean(vin), persist: false },
)
```

Сохранить существующий `staleHint` при ошибке refresh (если был snapshot).

- [ ] **Step 2: Section payload**

```tsx
const sectionRes = useCachedResource(
  vin && sectionId ? `emergency:section:${vin}:${sectionId}` : '',
  () => api.emergencySection(vin, sectionId),
  { enabled: Boolean(vin && sectionId), persist: false },
)
```

- [ ] **Step 3: Resolve** — одноразовый resolve по query можно не кэшировать (или короткий key `emergency:resolve:{query}` mem-only). Не ломать map/HUD.

- [ ] **Step 4: Ручная проверка** — открыть того же VIN повторно → HUD/snapshot без ожидания ручки.

---

### Task 4: OperatorParks SWR (Should)

**Files:**
- Modify: `apps/web/src/pages/OperatorParks.tsx`

- [ ] **Step 1: Три ресурса**

```tsx
const parksRes = useCachedResource('operator:parks', () => api.operatorParks())
const availableRes = useCachedResource('operator:available-parks', () => api.availableParks())
const requestsRes = useCachedResource('operator:park-requests', () => api.operatorParkRequests())
```

После `requestPark` — `requestsRes.refresh()` + при необходимости invalidate available.

- [ ] **Step 2: Smoke** — повторный заход без skeleton.

---

### Task 5: Admin screens (Could — skip unless asked)

**Files:** `Admin.tsx`, `AdminEmergencyConfig.tsx`

- [ ] Optional: один `useCachedResource('admin:bootstrap', () => Promise.all([...]))` или отдельные keys. Низкий приоритет.

---

### Task 6: Закрытие эпика

- [ ] **Step 1:** В спеке выставить **Status: implemented** (или `implemented (Must); Should backlog` если Emergency/Parks отложили)
- [ ] **Step 2:** Отметить чекбоксы Done/Todo в спеке
- [ ] **Step 3:** Один PR «feat(web,api): stale-while-revalidate for Tracker screens» **или** два: backend cache / frontend SWR — спросить пользователя (spec open Q5)
- [ ] **Step 4:** `gitarius scan` перед commit; не добавлять `apps/api/logs/`

---

## Test commands

```bash
# API cache unit/integration
cd apps/api && .venv/bin/pytest tests/test_response_cache.py tests/test_tracker_cache.py -q

# Broader regression (optional)
.venv/bin/pytest tests/test_emergency_router.py tests/test_mechanic_emergency.py -q

# Web typecheck if available
cd apps/web && npx tsc --noEmit
```

---

## Stop conditions

- Must = Task 1 + Task 2 green → можно идти к следующему пункту фидбека пользователя
- Should = Task 3–4 когда скажут «во всех разделах» включая Emergency/парки
- Не блокировать следующий UX-пункт пользователя на Could (Admin)
