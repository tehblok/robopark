# Robopark Phase 3 Reports and Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пересобрать репорты вокруг входящей очереди, канонических list/detail/new-маршрутов, существующих вложений и восстанавливаемого локального черновика, а аналитику — вокруг только реально доступной истории блокеров и сравнения парков без выдуманных SLA или повторяемости неисправностей.

**Architecture:** Phase 3 сохраняет публичные FastAPI URL/DTO и выполняет frontend-пересборку, но исправляет два внутренних authorization contracts без расширения данных: report inbox/actions переходят с закрытого списка role slugs на effective permission + fail-closed park/fleet scope, а `/dashboard/history` проверяет `nav.analytics`, тогда как `/dashboard/summary` продолжает проверять `nav.dashboard`. Транспорт остаётся в `apps/web/src/api.ts`, чистые преобразования — в `domains/reports` и `domains/analytics`, а route/nav-доступ расширяет единый foundation `ROUTE_MANIFEST`. `ReportWorkspace` всегда держит очередь главным контекстом, сохраняет фильтры в URL и открывает detail рядом на широком экране или отдельным экраном на телефоне; Analytics использует `/dashboard/history` для временного ряда и `/operator/now-report` только для недублирующего межпаркового сравнения.

**Tech Stack:** React 19.2.8, TypeScript 6.0.3, React Router 7.18.2, Vite 8.2.2, Vitest 4.1.11, Testing Library 16.3.2, Playwright и `@axe-core/playwright` из Phase 1, существующий FastAPI REST API, CSS variables и Lucide icons через foundation `Icon` wrapper.

**Spec:** [`docs/superpowers/specs/2026-09-02-robopark-product-redesign-design.md`](../specs/2026-09-02-robopark-product-redesign-design.md)

---

## Global Constraints

- Сначала полностью выполнить и проверить [`docs/superpowers/plans/2026-09-02-01-robopark-foundation.md`](./2026-09-02-01-robopark-foundation.md); Phase 3 расширяет его `RouteManifest`, `AppRouter`, design-system primitives и E2E harness, а не создаёт параллельные аналоги.
- По порядку миграции Phase 3 следует после [`docs/superpowers/plans/2026-09-02-02-robopark-operational-core.md`](./2026-09-02-02-robopark-operational-core.md). Phase 2 владеет `domains/shift/*` и не переносит туда `/dashboard/history`; Phase 3 единолично владеет историческим графиком и не меняет файлы Overview.
- Публичные API URL сохраняются без изменений: `GET /reports/inbox`, `GET /reports/mine`, `GET /reports/{id}`, `POST /reports`, lifecycle endpoints, attachment endpoints, `GET /dashboard/history` и `GET /operator/now-report`.
- Attachment storage и существующие response DTO не меняются. Report backend получает capability-driven service/router policy для custom roles и additive idempotency contract для `POST /reports`; соответствующая nullable model column, migration и optional request header перечислены в Task 5 и не ломают legacy callers.
- Не добавлять production-зависимости. Иконки брать только из foundation `Icon` wrapper; разрешённые Phase 3 names: `reports`, `analytics`, `attachment`, `camera`, `download`, `send`, `filter`, `clock`, `assignee`, `back`, `refresh`, `warning`, `success`, `info`.
- Backend остаётся источником истины по авторизации. UI capability-функции лишь скрывают заведомо недоступные действия; любой `403` всё равно показывается локально и не преобразуется в успех.
- Не показывать SLA, SLA-фильтр, именованного ответственного или повторяемые неисправности: в текущем `ReportOut`, `DashboardHistoryOut` и `NowReportOut` нет `due_at`, `sla_state`, `assignee`, fault taxonomy или достоверной repair history.
- Доступные report-фильтры в этом этапе строго ограничены `view`, `status`, `age`, `kind`, `park`; `status` применяется к «Моим», потому что `/reports/inbox` возвращает только открытые записи.
- Timeline является явно подписанной производной сводкой из `created_at`, `updated_at`, `resolved_at`, `status`, `return_comment`, `kind` и `parent_report_id`, а не полноценным audit log.
- Локальный черновик хранит только текст, вид, парк, opaque `idempotencyKey`, признак начатой create-попытки и `createdReportId` в user-scoped `localStorage` не более 7 суток. `File`/Blob не сериализуется; после same-session reload/offline текст сохраняется, а фото выбирается повторно. Numeric user ID не является долговечной security-границей: Plan 02 `clearProtectedBrowserStorage()` удаляет все report-draft namespaces до login и на logout/refresh-401. Это не offline-командная очередь: ключ лишь делает повтор одного и того же create безопасным после неоднозначного transport outcome.
- Сервер допускает не более одного attachment каждого kind. UI вручную добавляет только `device_photo`, но показывает и скачивает все три существующих kind: `ui_snapshot`, `device_photo`, `client_log`.
- Фотография не заменяет название, номер репорта, статус или alt-текст. Использовать только нейтральные тестовые изображения; фотографии роботов от владельца проекта в Phase 3 не требуются.
- Не копировать логотипы, шрифты, коммерческие силуэты или фирменную геометрию Яндекса. Применять оригинальный Robopark Operational из foundation.
- Светлая, тёмная и системная темы должны работать без отдельной domain-темы; Phase 3 CSS использует только семантические foundation tokens.
- Breakpoints фиксированы: compact phone — до 599 px; phone/tablet — 600–899 px; split tablet — 900–1199 px; desktop — от 1200 px.
- На 320 px основной layout не имеет горизонтального скролла; таблицы превращаются в смысловые карточки; interactive target не меньше 44×44 px; mobile form controls не меньше 16 px.
- Каждый график имеет текстовое резюме, единицы измерения и явное состояние неполных данных; значения отсутствующих исторических bucket нельзя подменять нулём.
- Все action failures получают `role="alert"`; loading/success — `aria-live`; focus, клавиатура, 200% zoom, reduced motion и WCAG AA наследуют foundation contracts.
- Для каждого поведения сначала написать тест, увидеть ожидаемый RED, затем внести минимальную реализацию и увидеть GREEN. После каждого task запускать указанный regression set и делать отдельный коммит.
- E2E использует только детерминированный `installMockApi`; не запускать тесты против локальной или удалённой runtime-базы, не читать реальные репорты, имена, токены, cookies или attachment-файлы.
- После каждого task выполнять `git diff --check` и проверять `git diff --name-only`. После scoped `git add`, но до каждого `git commit`, обязательно выполнить `git diff --cached --check`, сверить `git diff --cached --name-only` с точным allowlist из `Files`/намеренных snapshots текущего task и остановиться при любом заранее staged, несвязанном, runtime, secret, database, log, trace или generated-blob path; чужой index не очищать и не перезаписывать.
- Старые `pages/Reports.tsx`, `components/reports/*`, `pages/OperatorNowReport.tsx` и legacy CSS не удалять в этом этапе: они становятся compatibility/dead code и удаляются централизованно в Phase 5. Видимые routes обязаны использовать новые domain-компоненты.

---

## Dependency Contracts

После Phase 1 должны существовать и оставаться единственными владельцами соответствующей политики:

```ts
// apps/web/src/app/routing/routeManifest.ts
export type UserRole = 'royal' | 'admin' | 'operator' | 'mechanic' | 'driver'
export type NavGroup = 'operations' | 'collaboration' | 'insights' | 'administration'
export type NavSurface = 'desktop' | 'mobile'
export type AccessPrerequisite = 'password-changed' | 'approved' | 'mechanic-has-park'
export type RouteNav = {
  group: NavGroup
  desktopOrder: number
  mobilePriority?: Partial<Record<UserRole, number>>
}
export type RouteManifestItem = {
  id: AppRouteId
  path: string
  legacyPaths?: readonly string[]
  label: string
  icon: IconName
  permission?: string
  prerequisites?: readonly AccessPrerequisite[]
  surface: 'public' | 'standalone' | 'shell'
  nav?: RouteNav
}
export const ROUTE_MANIFEST: readonly RouteManifestItem[]
```

```ts
// apps/web/src/app/routing/accessPolicy.ts
export type AccessUser = Pick<
  User,
  'role' | 'access_status' | 'permissions' | 'parks' | 'must_change_password'
>
export function canAccessRoute(user: AccessUser, routeId: AppRouteId): boolean
export function landingPathForUser(user: AccessUser): string
export function navigationForUser(
  user: AccessUser,
  surface: NavSurface,
): NavigationItem[]
```

`apps/web/src/app/routing/AppRouter.tsx` владеет `ROUTE_ELEMENTS: Record<AppRouteId, ReactElement>` и `RouteGate`. `apps/web/src/app/shell/AppShell.tsx` получает navigation из manifest и рендерит `<Outlet>`.

Foundation также единолично владеет park query-state; Phase 3 не валидирует park id повторно:

```ts
// apps/web/src/app/park/parkScope.ts и ParkScopeProvider.tsx
export const PARK_QUERY_KEY = 'park'
export type ParkScopeValue = {
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
  locked: boolean
  setParkId: (id: number, options?: { replace?: boolean }) => void
  refreshParks: () => Promise<void>
}
export function useParkScope(): ParkScopeValue
```

E2E расширяет только массив custom routes; сам foundation harness Phase 3 не меняет:

```ts
// apps/web/e2e/support/mockApi.ts
export type MockResponse = {
  status?: number
  json?: unknown
  body?: string
  headers?: Record<string, string>
}
export type MockRouteHandler = (
  request: Request,
) => MockResponse | Promise<MockResponse>
export type MockRoute = {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string | RegExp
  handler: MockRouteHandler
}
export type MockApiOptions = {
  user?: User | null
  parks?: Park[]
  routes?: readonly MockRoute[]
}
```

Строковый `MockRoute.path` — точный pathname с `/api` без query; `RegExp` сопоставляется с pathname. Custom routes обрабатываются до foundation defaults.

Phase 3 использует foundation primitives с этими сигнатурами:

```ts
LoadingState({ label, variant }: { label: string; variant?: 'inline' | 'panel' | 'page' })
EmptyState({ title, description, icon, action }: {
  title: string
  description?: string
  icon?: IconName
  action?: ReactNode
})
ErrorState({ title, description, onRetry, retryLabel, requestId }: {
  title: string
  description: string
  onRetry?: () => void
  retryLabel?: string
  requestId?: string
})
StaleBadge({ state, updatedAt, label }: {
  state: 'live' | 'fresh' | 'stale' | 'offline'
  updatedAt?: string
  label?: string
})
Button({ variant, size, leadingIcon, busy, ...buttonProps })
IconButton({ label, icon, ...buttonProps })
StatusBadge({ tone, icon, children })
PageLayout({ title, description, eyebrow, actions, children })
Panel({ title, description, actions, children })
FormField({ id, label, hint, error, required, children })
```

Phase 2 предоставляет, а Phase 3 только импортирует:

```ts
// apps/web/src/shared/api/classifyApiError.ts
export type DomainErrorKind =
  | 'offline'
  | 'timeout'
  | 'unauthorized'
  | 'forbidden'
  | 'not-found'
  | 'conflict'
  | 'configuration'
  | 'server'
  | 'unknown'
export type DomainError = {
  kind: DomainErrorKind
  title: string
  description: string
  retryable: boolean
  requestId?: string
}
export function classifyApiError(error: unknown, fallback: string): DomainError
```

## Confirmed Existing Server Contract

| API | Подтверждённые данные | Phase 3 usage |
| --- | --- | --- |
| `GET /reports/inbox?park_id=` | Только открытые входящие, newest-first; optional server park filter | Главная очередь для пользователей с `reports.resolve` |
| `GET /reports/mine` | Все репорты текущего автора, newest-first | «Мои» и восстановление созданного репорта |
| `GET /reports/{id}` | Полный `ReportOut` с attachment metadata | Канонический deep-link detail |
| `POST /reports` | JSON `ReportCreateIn`, optional backward-compatible `Idempotency-Key`, `201 ReportOut` | Composer; один client key + author создаёт не более одной записи и безопасно возвращает её после потерянного response |
| `POST /reports/{id}/return` | Обязательный непустой `comment` | Возврат с явным комментарием |
| `POST /reports/{id}/done` | Без comment, выставляет `resolved_at` | Завершение без выдуманного комментария |
| `POST /reports/{id}/escalate` | Assigned-park resolver of an operator-target report, обязательный comment, возвращает новый child report | Эскалация и переход к child detail; system operator behavior сохраняется, capable custom roles не исключаются по slug |
| `POST /reports/{id}/attachments` | Multipart `kind` + `file`, только author/open, один kind | `device_photo` из камеры или файла |
| `GET /reports/{id}/attachments/{attachment_id}` | Raw file, тот же view ACL, исходное имя | Preview/download существующих вложений |
| `GET /dashboard/history?park_id=&days=` | `1..30` дней, реальные 2-hour buckets `arrived_count/departed_count` | Единственный исторический trend |
| `GET /operator/now-report` | Operator-only current per-park metrics, `generated_at`, `skipped_parks` | Только cross-park comparison и причины пропусков; totals не выводятся |

`ReportOut` не содержит пользователя автора, назначенного исполнителя, deadline/SLA, историю переходов или attachment timestamps. `NowReportOut` не является историей и не должен называться SLA.

## File Map and Ownership

| File | Responsibility |
| --- | --- |
| `apps/api/src/robopark_api/models.py`, `alembic/versions/0016_report_idempotency.py` | Nullable per-author client request key and additive uniqueness for ambiguous create recovery |
| `apps/api/src/robopark_api/services/reports.py` | Effective permission + assigned/fleet scope for inbox, direct view, lifecycle actions, escalation and badge; no public URL/DTO change |
| `apps/api/src/robopark_api/routers/reports.py` | Capability-based escalation dependency in Task 2 and backward-compatible optional create-idempotency header in Task 5 |
| `apps/api/tests/test_reports.py` | Existing role regressions plus real custom-role permission/scope HTTP matrix |
| `apps/web/src/api.ts` | Точные report/attachment transport types и multipart/download helpers; существующие endpoint names сохраняются |
| `apps/web/src/api.report-attachments.test.ts` | Transport regression: FormData без JSON header, kind/file и same-origin download URL |
| `apps/web/src/domains/reports/reportModel.ts` | URL filters, queue filtering, capability derivation, labels, bytes/date formatting и честный lifecycle timeline |
| `apps/web/src/domains/reports/reportModel.test.ts` | Pure deterministic tests report model |
| `apps/web/src/domains/reports/DevicePhotoPicker.tsx` | Один доступный camera/file picker без persistence обещания |
| `apps/web/src/domains/reports/ReportAttachments.tsx` | Preview/download всех attachment kinds и author/open upload slot для `device_photo` |
| `apps/web/src/domains/reports/ReportTimeline.tsx` | Derived lifecycle summary с disclosure о неполном журнале |
| `apps/web/src/domains/reports/ReportLifecycleActions.tsx` | Return/done/escalate controls и обязательные comments только для поддержанных endpoints |
| `apps/web/src/domains/reports/ReportDetailPanel.tsx` | Композиция полей, адресата, Tracker link, timeline, evidence и actions |
| `apps/web/src/domains/reports/ReportDetailPanel.test.tsx` | Component behavior, accessibility и отсутствие unsupported promises |
| `apps/web/src/domains/reports/ReportQueue.tsx` | Семантическая queue list/card view, selected state и links с сохранённым search |
| `apps/web/src/domains/reports/ReportFiltersBar.tsx` | Только status/age/kind/park/view controls, разрешённые текущим контрактом |
| `apps/web/src/domains/reports/ReportWorkspacePage.tsx` | Data controller для list/detail routes, inbox-first default, mutations, cache/badge invalidation и responsive split |
| `apps/web/src/domains/reports/ReportWorkspacePage.test.tsx` | List/detail deep-link, URL persistence, loading/error/empty и inbox-first tests |
| `apps/web/src/domains/reports/reportDraft.ts` | Versioned, user-scoped, expiring local draft with a pre-request idempotency key, ambiguous-attempt state, and post-response `createdReportId` checkpoint |
| `apps/web/src/domains/reports/reportDraft.test.ts` | Corrupt/expired/cross-user/reused-ID/session-boundary/created-id recovery tests |
| `apps/web/src/domains/reports/ReportComposerPage.tsx` | `/reports/new`, validation, autosave, create-then-attach retry и canonical navigation |
| `apps/web/src/domains/reports/ReportComposerPage.test.tsx` | Draft reload, camera/file attributes, POST/upload retry without duplicate create |
| `apps/web/src/domains/reports/reports.css` | Domain-only responsive queue/detail/composer styling on foundation tokens |
| `apps/web/src/domains/analytics/analyticsModel.ts` | Query state, bucket aggregation without invented zeroes, coverage, summaries, feature gates and park comparison |
| `apps/web/src/domains/analytics/analyticsModel.test.ts` | Timezone, partial coverage, units, sorting and unsupported-section tests |
| `apps/web/src/domains/analytics/BlockerFlowPanel.tsx` | Accessible historical SVG/table, summary, unit, source and freshness |
| `apps/web/src/domains/analytics/ParkComparisonPanel.tsx` | Operator-only comparison and skipped-park reasons, no duplicate total KPI |
| `apps/web/src/domains/analytics/AnalyticsPage.tsx` | URL/controller/error/empty orchestration for available sections only |
| `apps/web/src/domains/analytics/AnalyticsPage.test.tsx` | API gating, no placeholders/SLA, source/summary/incomplete-data component tests |
| `apps/web/src/domains/analytics/analytics.css` | Responsive chart/table/card styles using design-system tokens |
| `apps/web/src/app/routing/routeManifest.ts` | Reuse Foundation `reports`/`analytics`, add only `report-detail`/`report-new`, and replace their manifest metadata; no local role/nav table |
| `apps/web/src/app/routing/AppRouter.tsx` | Map Phase 3 ids to domain page elements under existing RouteGate |
| `apps/web/src/app/routing/routeManifest.test.ts` | Canonical path, non-nav detail/new, permission and nav-group parity |
| `apps/web/src/pages/Reports.tsx` | Temporary compatibility re-export only; deletion remains Phase 5 |
| `apps/web/src/pages/Analytics.tsx` | Temporary compatibility re-export only; deletion remains Phase 5 |
| `apps/web/src/pages/Analytics.test.tsx` | Replace obsolete placeholder assertion with compatibility export regression |
| `apps/web/src/i18n/ru.ts` | Approved Russian report/analytics copy and attachment API error mappings |
| `apps/web/e2e/support/mockApi.ts` | Foundation dependency consumed unchanged; Phase 3 endpoint fixtures are supplied through `MockApiOptions.routes` |
| `apps/web/e2e/reports/fixtures.ts` | Stateful deterministic report routes and neutral fixtures; no runtime reads |
| `apps/web/e2e/reports/reports.spec.ts` | Desktop split, mobile composer/draft, direct links, actions, a11y and 320 px overflow |
| `apps/web/e2e/reports/reports.visual.spec.ts` | Light/dark snapshots at 320, 390, 768, 1024 and 1440 px |
| `apps/web/e2e/analytics/fixtures.ts` | Deterministic history/comparison payloads and route handlers |
| `apps/web/e2e/analytics/analytics.spec.ts` | Real-data, partial-data, role gate, no-SLA and a11y journeys |
| `apps/web/e2e/analytics/analytics.visual.spec.ts` | Light/dark snapshots at all five acceptance widths |

---

### Task 1: Type and expose the existing report attachment transport

**Files:**
- Create: `apps/web/src/api.report-attachments.test.ts`
- Modify: `apps/web/src/api.ts:327-358`
- Modify: `apps/web/src/api.ts:782-812`
- Modify: `apps/web/src/i18n/ru.ts:103-172`

**Interfaces:**
- Consumes: existing private `requestForm<T>(path, formData)` and authenticated `/api` transport.
- Produces: `ReportStatus`, `ReportAttachmentKind`, `ReportAttachment`, corrected `Report.park_id`, required `Report.attachments`, `api.addReportAttachment(reportId, kind, file)`, `reportAttachmentHref(reportId, attachmentId)`.

- [ ] **Step 1: Write the failing transport test**

Create `apps/web/src/api.report-attachments.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  reportAttachmentHref,
  type ReportAttachment,
} from './api'

const attachment: ReportAttachment = {
  id: 9,
  kind: 'device_photo',
  filename: 'robot-447.jpg',
  content_type: 'image/jpeg',
  size_bytes: 4,
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('report attachment transport', () => {
  it('posts multipart kind and file without a JSON content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(attachment), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['jpeg'], 'robot-447.jpg', { type: 'image/jpeg' })

    await expect(api.addReportAttachment(41, 'device_photo', file)).resolves.toEqual(
      attachment,
    )

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/reports/41/attachments')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
    expect(init.headers).toBeUndefined()
    const body = init.body as FormData
    expect(body.get('kind')).toBe('device_photo')
    expect(body.get('file')).toBe(file)
  })

  it('builds an authenticated same-origin download URL', () => {
    expect(reportAttachmentHref(41, 9)).toBe('/api/reports/41/attachments/9')
  })
})
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```sh
cd apps/web
npm test -- src/api.report-attachments.test.ts
```

Expected: FAIL because `ReportAttachment`, `reportAttachmentHref` and `api.addReportAttachment` are not exported.

- [ ] **Step 3: Add exact report and attachment types**

Replace the report type block in `apps/web/src/api.ts` with:

```ts
export type ReportKindManual = 'ticket_question' | 'mechanic_problem'
export type ReportStatus = 'open' | 'returned' | 'done'
export type ReportAttachmentKind = 'ui_snapshot' | 'device_photo' | 'client_log'

export type ReportAttachment = {
  id: number
  kind: ReportAttachmentKind
  filename: string
  content_type: string
  size_bytes: number
}

export type Report = {
  id: number
  kind: string
  status: ReportStatus
  park_id: number | null
  author_user_id: number
  target_role: string
  tracker_key: string | null
  tracker_url: string | null
  title: string
  body: string
  parent_report_id: number | null
  return_comment: string | null
  created_at: string
  updated_at: string
  resolved_at: string | null
  attachments: ReportAttachment[]
}
```

Add the pure URL helper immediately before `export const api`:

```ts
export function reportAttachmentHref(reportId: number, attachmentId: number): string {
  return `/api/reports/${encodeURIComponent(String(reportId))}/attachments/${encodeURIComponent(String(attachmentId))}`
}
```

Add this method beside the existing report methods:

```ts
  addReportAttachment: (
    reportId: number,
    kind: ReportAttachmentKind,
    file: File,
  ) => {
    const form = new FormData()
    form.append('kind', kind)
    form.append('file', file, file.name)
    return requestForm<ReportAttachment>(
      `/reports/${encodeURIComponent(String(reportId))}/attachments`,
      form,
    )
  },
```

Do not expose `storage_key` and do not fetch download blobs into memory; the authenticated same-origin link uses the server `Content-Disposition` filename.

- [ ] **Step 4: Map existing attachment errors to specific Russian copy**

Add these entries to `ru.errors.details` in `apps/web/src/i18n/ru.ts`:

```ts
      report_attachment_empty: 'Файл пустой.',
      report_attachment_too_large: 'Файл слишком большой: изображение — до 15 МБ.',
      report_attachment_invalid_type: 'Поддерживаются JPEG, PNG, WebP и HEIC.',
      report_attachment_kind_invalid: 'Этот тип вложения не поддерживается.',
      report_attachment_kind_taken: 'Фото уже прикреплено к репорту.',
```

- [ ] **Step 5: Run GREEN and transport regressions**

Run:

```sh
cd apps/web
npm test -- src/api.report-attachments.test.ts src/i18n/errors.test.ts
npm run build
git diff --check
```

Expected: both test files pass and TypeScript accepts `park_id: number | null` plus required `attachments`.

- [ ] **Step 6: Commit**

```sh
git add apps/web/src/api.ts apps/web/src/api.report-attachments.test.ts apps/web/src/i18n/ru.ts
git commit -m "feat(web): expose report attachment transport"
```

---

### Task 2: Define honest report filters, capabilities, and lifecycle projection

**Files:**
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/src/robopark_api/routers/reports.py`
- Modify: `apps/api/tests/test_reports.py`
- Create: `apps/web/src/domains/reports/reportModel.ts`
- Create: `apps/web/src/domains/reports/reportModel.test.ts`

**Interfaces:**
- Consumes: backend RBAC permissions/assigned-park scope; frontend `AccessUser`, `User`, `Report`, `ReportStatus`, `ReportAttachmentKind`, `hasFleetParkScope`.
- Produces: `ReportQueueView`, `ReportAgeFilter`, `ReportKindFilter`, `ReportFilterState`, `ReportActionCapabilities`, `ReportTimelineEvent`, `defaultReportView`, `canCreateReport`, `parseReportFilters`, `serializeReportFilters`, `filterReportQueue`, `reportActionsFor`, `buildReportTimeline`, `targetRoleLabel`, `formatReportDate`, `formatBytes`.
- Policy invariant: custom role slugs are never silently excluded. An approved actor with `reports.resolve` and assigned park scope receives the operator-target queue for those parks; admin/royal or an actor explicitly granted `parks.manage` receives the fleet/admin-target queue. The target audience remains meaningful, and backend/frontend derive the same capability from permission plus scope rather than a closed role list.

- [ ] **Step 1: Write failing model tests**

Create `apps/web/src/domains/reports/reportModel.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { Report, User } from '../../api'
import {
  buildReportTimeline,
  canCreateReport,
  defaultReportView,
  filterReportQueue,
  parseReportFilters,
  reportActionsFor,
  serializeReportFilters,
} from './reportModel'

const park = { id: 7, name: 'Север', tag: 'NORTH' }
const operator: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create', 'reports.resolve'],
  parks: [park],
}
const mechanic: User = {
  id: 3,
  username: 'mechanic',
  role: 'mechanic',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create'],
  parks: [park],
}
const report: Report = {
  id: 41,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 3,
  target_role: 'operator',
  tracker_key: null,
  tracker_url: null,
  title: 'Не включается лидар',
  body: 'После перезапуска ошибка остаётся.',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-01T07:00:00Z',
  updated_at: '2026-09-01T07:00:00Z',
  resolved_at: null,
  attachments: [],
}

describe('report filter state', () => {
  it('defaults a resolver to inbox and a creator to mine', () => {
    expect(defaultReportView(operator)).toBe('inbox')
    expect(defaultReportView(mechanic)).toBe('mine')
    expect(canCreateReport(operator)).toBe(true)
    expect(canCreateReport({ ...operator, parks: [] })).toBe(false)
    expect(canCreateReport({ ...operator, permissions: ['reports.create'] })).toBe(false)
  })

  it('round-trips supported URL filters and drops unsupported SLA/assignee keys', () => {
    const search = '?view=mine&status=returned&age=72h&kind=mechanic_problem&park=7&sla=late&assignee=2'
    const filters = parseReportFilters(search, { user: operator, parkId: 7 })

    expect(filters).toEqual({
      view: 'mine',
      status: 'returned',
      age: '72h',
      kind: 'mechanic_problem',
      parkId: 7,
    })
    expect(serializeReportFilters(filters, search)).toBe(
      '?view=mine&status=returned&age=72h&kind=mechanic_problem&park=7',
    )
    expect(parseReportFilters('?view=inbox&status=done', {
      user: operator,
      parkId: 7,
    }).status).toBe('all')
  })

  it('filters by mutually exclusive age windows without treating invalid dates as old', () => {
    const now = new Date('2026-09-02T12:00:00Z')
    const reports = [
      { ...report, id: 1, created_at: '2026-09-02T00:30:00Z' },
      { ...report, id: 2, created_at: '2026-08-31T12:00:00Z' },
      { ...report, id: 3, created_at: '2026-08-29T12:00:00Z' },
      { ...report, id: 4, created_at: 'not-a-date' },
    ]

    expect(filterReportQueue(reports, {
      view: 'mine', status: 'all', age: '24h', kind: 'all', parkId: null,
    }, now).map((item) => item.id)).toEqual([1])
    expect(filterReportQueue(reports, {
      view: 'mine', status: 'all', age: '72h', kind: 'all', parkId: null,
    }, now).map((item) => item.id)).toEqual([2])
    expect(filterReportQueue(reports, {
      view: 'mine', status: 'all', age: 'older', kind: 'all', parkId: null,
    }, now).map((item) => item.id)).toEqual([3])
  })
})

describe('report capabilities and timeline', () => {
  it('matches current server recipient and author rules', () => {
    expect(reportActionsFor(operator, report)).toEqual({
      canReturn: true,
      canComplete: true,
      canEscalate: true,
      canAttachDevicePhoto: false,
    })
    expect(reportActionsFor(mechanic, report)).toEqual({
      canReturn: false,
      canComplete: false,
      canEscalate: false,
      canAttachDevicePhoto: true,
    })
    expect(reportActionsFor(mechanic, {
      ...report,
      attachments: [{
        id: 9,
        kind: 'device_photo',
        filename: 'robot.jpg',
        content_type: 'image/jpeg',
        size_bytes: 10,
      }],
    }).canAttachDevicePhoto).toBe(false)
  })

  it('derives only events supported by persisted fields', () => {
    const returned: Report = {
      ...report,
      status: 'returned',
      return_comment: 'Добавьте фото разъёма.',
      updated_at: '2026-09-01T09:00:00Z',
    }

    expect(buildReportTimeline(returned)).toEqual([
      {
        id: 'created',
        label: 'Репорт создан',
        at: '2026-09-01T07:00:00Z',
        detail: null,
        tone: 'neutral',
        timeBasis: 'exact',
      },
      {
        id: 'returned',
        label: 'Возвращён автору',
        at: '2026-09-01T09:00:00Z',
        detail: 'Добавьте фото разъёма.',
        tone: 'warning',
        timeBasis: 'last-update',
      },
    ])
    expect(buildReportTimeline(report).map((event) => event.id)).toEqual(['created'])
  })
})
```

Extend the capability test with an approved `field_lead` carrying `reports.resolve` and the assigned park: it can return/complete/escalate the operator-target report. Removing its parks makes every resolve action false. A second custom actor with `reports.resolve` plus `parks.manage` and no assigned parks can return/complete an admin-target report but cannot escalate it. Pending/rejected actors always receive no resolve action even when the permission is present.

In `apps/api/tests/test_reports.py`, seed real custom `Role` rows and attach catalog permissions instead of mutating a username or role string in memory. Add HTTP RED cases proving the same policy end to end:

1. assigned `field_lead` + `reports.resolve` lists, reads, returns, completes, and escalates an operator-target report in its park;
2. the same actor cannot list/read/mutate a report from an unassigned park;
3. a custom fleet role with `reports.resolve` + `parks.manage` receives and resolves the admin-target inbox without an assignment;
4. a custom role with only `nav.reports`, or a pending actor with `reports.resolve`, receives `403` from inbox/mutation endpoints;
5. existing operator/admin/royal behavior remains unchanged.

- [ ] **Step 2: Run the pure tests and confirm RED**

Run:

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_reports.py

cd ../web
npm test -- src/domains/reports/reportModel.test.ts
```

Expected: backend custom-role cases fail against the role-slug branches, and web fails because `reportModel.ts` does not exist.

- [ ] **Step 3: Implement the exact report state types and URL parser**

Create `apps/web/src/domains/reports/reportModel.ts` with these exported types and parser constants:

```ts
import type { AccessUser } from '../../app/routing/accessPolicy'
import { hasFleetParkScope, PARK_QUERY_KEY } from '../../app/park/parkScope'
import type { Report, ReportStatus, User } from '../../api'

export type ReportQueueView = 'inbox' | 'mine'
export type ReportAgeFilter = 'all' | '24h' | '72h' | 'older'
export type ReportKindFilter =
  | 'all'
  | 'ticket_question'
  | 'ticket_close_review'
  | 'mechanic_problem'
  | 'escalation_to_admin'
  | 'emergency_cookie_stale'

export type ReportFilterState = {
  view: ReportQueueView
  status: 'all' | ReportStatus
  age: ReportAgeFilter
  kind: ReportKindFilter
  parkId: number | null
}

export const SUPPORTED_REPORT_FILTERS = [
  'view',
  'status',
  'age',
  'kind',
  'park',
] as const

const statuses = new Set(['all', 'open', 'returned', 'done'])
const ages = new Set(['all', '24h', '72h', 'older'])
const kinds = new Set([
  'all',
  'ticket_question',
  'ticket_close_review',
  'mechanic_problem',
  'escalation_to_admin',
  'emergency_cookie_stale',
])

function permissions(user: AccessUser): Set<string> {
  return new Set(user.permissions ?? [])
}

export function defaultReportView(user: AccessUser): ReportQueueView {
  return permissions(user).has('reports.resolve') ? 'inbox' : 'mine'
}

export function canCreateReport(user: AccessUser): boolean {
  const allowed = permissions(user)
  return user.access_status === 'approved'
    && !user.must_change_password
    && allowed.has('nav.reports')
    && allowed.has('reports.create')
    && user.parks.length > 0
}

export function parseReportFilters(
  search: string,
  options: {
    user: AccessUser
    parkId: number | null
  },
): ReportFilterState {
  const query = new URLSearchParams(search)
  const fallbackView = defaultReportView(options.user)
  const requestedView = query.get('view')
  const view = requestedView === 'mine'
    ? 'mine'
    : requestedView === 'inbox' && permissions(options.user).has('reports.resolve')
      ? 'inbox'
      : fallbackView
  const rawStatus = query.get('status') ?? 'all'
  const rawAge = query.get('age') ?? 'all'
  const rawKind = query.get('kind') ?? 'all'

  return {
    view,
    status: (view === 'inbox'
      ? 'all'
      : statuses.has(rawStatus) ? rawStatus : 'all') as ReportFilterState['status'],
    age: (ages.has(rawAge) ? rawAge : 'all') as ReportAgeFilter,
    kind: (kinds.has(rawKind) ? rawKind : 'all') as ReportKindFilter,
    parkId: options.parkId,
  }
}

export function serializeReportFilters(
  filters: ReportFilterState,
  currentSearch: string,
): string {
  const current = new URLSearchParams(currentSearch)
  const query = new URLSearchParams()
  query.set('view', filters.view)
  query.set('status', filters.status)
  query.set('age', filters.age)
  query.set('kind', filters.kind)
  const park = current.get(PARK_QUERY_KEY)
  if (park) query.set(PARK_QUERY_KEY, park)
  return `?${query.toString()}`
}
```

- [ ] **Step 4: Implement deterministic filtering and capability projection**

Append to `reportModel.ts`:

```ts
const HOUR_MS = 60 * 60 * 1000

export function filterReportQueue(
  reports: readonly Report[],
  filters: ReportFilterState,
  now: Date = new Date(),
): Report[] {
  return reports.filter((report) => {
    if (filters.status !== 'all' && report.status !== filters.status) return false
    if (filters.kind !== 'all' && report.kind !== filters.kind) return false
    if (filters.parkId != null && report.park_id !== filters.parkId) return false
    if (filters.age === 'all') return true
    const createdAt = new Date(report.created_at).getTime()
    if (!Number.isFinite(createdAt)) return false
    const hours = Math.max(0, (now.getTime() - createdAt) / HOUR_MS)
    if (filters.age === '24h') return hours <= 24
    if (filters.age === '72h') return hours > 24 && hours <= 72
    return hours > 72
  })
}

export type ReportActionCapabilities = {
  canReturn: boolean
  canComplete: boolean
  canEscalate: boolean
  canAttachDevicePhoto: boolean
}

export function reportActionsFor(
  user: User,
  report: Report,
): ReportActionCapabilities {
  const userPermissions = permissions(user)
  const assignedPark = report.park_id != null
    && user.parks.some((park) => park.id === report.park_id)
  const fleetScope = hasFleetParkScope(user)
  const recipient = report.target_role === 'admin'
    ? fleetScope
    : report.target_role === 'operator'
      ? !fleetScope && assignedPark
      : false
  const canResolve = report.status === 'open'
    && user.access_status === 'approved'
    && !user.must_change_password
    && userPermissions.has('reports.resolve')
    && recipient
  const hasDevicePhoto = report.attachments.some(
    (attachment) => attachment.kind === 'device_photo',
  )

  return {
    canReturn: canResolve,
    canComplete: canResolve,
    canEscalate: canResolve && report.target_role === 'operator',
    canAttachDevicePhoto: report.status === 'open'
      && report.author_user_id === user.id
      && userPermissions.has('reports.create')
      && !hasDevicePhoto,
  }
}
```

In `apps/api/src/robopark_api/services/reports.py`, replace `_is_admin_inbox_user` and `_is_approved_operator` with permission-and-scope helpers that call `rbac.has_permission` against the real session:

- `is_approved_resolver(db, user)` requires approved access plus `reports.resolve`;
- `has_fleet_report_scope(db, user)` is true only for admin/royal or effective `parks.manage`;
- `assigned_report_park_ids(db, user)` returns the assigned IDs only for an approved resolver without fleet scope.

Apply them to **every** list/view/action/badge/escalation branch, not just the router dependency. Fleet resolvers keep the admin-target inbox; assigned resolvers keep the operator-target inbox limited to their assigned park IDs. A report author may still read their own report. Fleet resolvers may directly read either supported target for support/audit continuity, but can mutate only admin-target reports; assigned resolvers can read/mutate only operator-target reports in their park. Escalation requires a mutable operator-target report in assigned scope and therefore works for a capable custom role without checking `user.role`. Unknown `target_role` remains fail-closed.

For badges, first select the effective resolver queue using the same scope helper; otherwise an actor with `reports.create` receives only their own returned-report count. Preserve the optional `park_id` authorization and the existing emergency-cookie inclusion rule for the fleet queue. Delete the two closed-role helpers and assert `rg -n "_is_admin_inbox_user|_is_approved_operator" apps/api/src/robopark_api/services/reports.py` returns no matches.

In `apps/api/src/robopark_api/routers/reports.py`, remove the now-unused `require_approved_operator` import and change only `POST /reports/{report_id}/escalate` to `user: User = Depends(_require_inbox_viewer)`. The dependency proves approved `reports.resolve`; `reports_svc.escalate_report` remains the authoritative target-role and assigned-park check. Return/done/direct-view routes continue through `require_user` and the same service policy, so no router guard can reintroduce a closed role-slug list.

- [ ] **Step 5: Implement the derived timeline and formatters without inventing events**

Append to `reportModel.ts`:

```ts
export type ReportTimelineEvent = {
  id: 'created' | 'returned' | 'completed'
  label: string
  at: string | null
  detail: string | null
  tone: 'neutral' | 'warning' | 'success'
  timeBasis: 'exact' | 'last-update' | 'missing'
}

export function buildReportTimeline(report: Report): ReportTimelineEvent[] {
  const events: ReportTimelineEvent[] = [{
    id: 'created',
    label: report.kind === 'escalation_to_admin'
      ? 'Эскалация создана'
      : 'Репорт создан',
    at: report.created_at,
    detail: report.parent_report_id == null
      ? null
      : `Связан с репортом #${report.parent_report_id}`,
    tone: 'neutral',
    timeBasis: 'exact',
  }]

  if (report.status === 'returned') {
    events.push({
      id: 'returned',
      label: 'Возвращён автору',
      at: report.updated_at || null,
      detail: report.return_comment,
      tone: 'warning',
      timeBasis: report.updated_at ? 'last-update' : 'missing',
    })
  }

  if (report.status === 'done') {
    events.push({
      id: 'completed',
      label: 'Завершён',
      at: report.resolved_at,
      detail: null,
      tone: 'success',
      timeBasis: report.resolved_at ? 'exact' : 'missing',
    })
  }

  return events
}

export function targetRoleLabel(role: string): string {
  if (role === 'operator') return 'Операторы парка'
  if (role === 'admin') return 'Администраторы'
  return role
}

export function formatReportDate(value: string | null): string {
  if (!value) return 'Время не записано'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Время не записано'
  return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} Б`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} КБ`
  return `${(value / (1024 * 1024)).toFixed(1)} МБ`
}
```

The `timeBasis: 'last-update'` marker must be rendered as «время последнего изменения», not as an exact audit timestamp.

- [ ] **Step 6: Run GREEN and static checks**

Run:

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_reports.py
uv run --frozen --extra dev ruff check src/robopark_api/services/reports.py src/robopark_api/routers/reports.py tests/test_reports.py

cd ../web
npm test -- src/domains/reports/reportModel.test.ts
npm run lint
npm run build
git diff --check
```

Expected: backend system/custom role policy cases and report model tests pass; the two closed-role helper names are absent; no `sla`, `assignee`, `due_at` or synthetic history field appears in the production model.

- [ ] **Step 7: Commit**

```sh
git add \
  apps/api/src/robopark_api/services/reports.py \
  apps/api/src/robopark_api/routers/reports.py \
  apps/api/tests/test_reports.py \
  apps/web/src/domains/reports/reportModel.ts \
  apps/web/src/domains/reports/reportModel.test.ts
git commit -m "feat(reports): align capability and scope policy"
```

---

### Task 3: Build report detail, evidence, lifecycle, and supported actions

**Files:**
- Create: `apps/web/src/domains/reports/DevicePhotoPicker.tsx`
- Create: `apps/web/src/domains/reports/ReportAttachments.tsx`
- Create: `apps/web/src/domains/reports/ReportTimeline.tsx`
- Create: `apps/web/src/domains/reports/ReportLifecycleActions.tsx`
- Create: `apps/web/src/domains/reports/ReportDetailPanel.tsx`
- Create: `apps/web/src/domains/reports/ReportDetailPanel.test.tsx`
- Create: `apps/web/src/domains/reports/reports.css`
- Modify: `apps/web/src/i18n/ru.ts:330-352`

**Interfaces:**
- Consumes: `Report`, `User`, `reportAttachmentHref`, Task 2 formatters/capabilities/timeline, foundation `Button`, `Icon`, `StatusBadge`, `Panel`, `FormField`.
- Produces: `DevicePhotoPicker({file, disabled, onFileChange})`; `ReportDetailPanel({report, user, parkName, search, onReturn, onComplete, onEscalate, onUploadPhoto})` where every mutation callback returns `Promise<void>`.

- [ ] **Step 1: Write the failing detail behavior tests**

Create `apps/web/src/domains/reports/ReportDetailPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { Report, User } from '../../api'
import { ReportDetailPanel } from './ReportDetailPanel'

const park = { id: 7, name: 'Север', tag: 'NORTH' }
const operator: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.resolve'],
  parks: [park],
}
const mechanic: User = {
  id: 3,
  username: 'mechanic',
  role: 'mechanic',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create'],
  parks: [park],
}
const report: Report = {
  id: 41,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 3,
  target_role: 'operator',
  tracker_key: 'ROBOPARK-41',
  tracker_url: 'https://st.yandex-team.ru/ROBOPARK-41',
  title: 'Не включается лидар',
  body: 'После перезапуска ошибка остаётся.',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-01T07:00:00Z',
  updated_at: '2026-09-01T07:00:00Z',
  resolved_at: null,
  attachments: [{
    id: 9,
    kind: 'ui_snapshot',
    filename: 'screen.png',
    content_type: 'image/png',
    size_bytes: 2048,
  }],
}

const noop = async () => {}

function renderDetail(user: User, overrides: Partial<Parameters<typeof ReportDetailPanel>[0]> = {}) {
  return render(
    <MemoryRouter>
      <ReportDetailPanel
        onComplete={noop}
        onEscalate={noop}
        onReturn={noop}
        onUploadPhoto={noop}
        parkName="Север"
        report={report}
        search="?view=inbox&status=all&age=all&kind=all&park=7"
        user={user}
        {...overrides}
      />
    </MemoryRouter>,
  )
}

describe('ReportDetailPanel', () => {
  it('shows persisted evidence and an honest derived timeline', () => {
    renderDetail(operator)

    expect(screen.getByRole('heading', { name: 'Не включается лидар' })).toBeInTheDocument()
    expect(screen.getByText('Этапы по текущему состоянию')).toBeInTheDocument()
    expect(screen.getByText(/не является полным журналом действий/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Скачать screen.png/i })).toHaveAttribute(
      'href',
      '/api/reports/41/attachments/9',
    )
    expect(screen.queryByText(/SLA/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Ответственный/i)).not.toBeInTheDocument()
  })

  it('requires a comment before returning a report', async () => {
    const onReturn = vi.fn().mockResolvedValue(undefined)
    renderDetail(operator, { onReturn })

    fireEvent.click(screen.getByRole('button', { name: 'Вернуть автору' }))
    const submit = screen.getByRole('button', { name: 'Подтвердить возврат' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Комментарий для автора'), {
      target: { value: 'Добавьте фото разъёма.' },
    })
    fireEvent.click(submit)

    await waitFor(() => {
      expect(onReturn).toHaveBeenCalledWith('Добавьте фото разъёма.')
    })
  })

  it('offers separate camera and file inputs only to the open-report author', async () => {
    const onUploadPhoto = vi.fn().mockResolvedValue(undefined)
    renderDetail(mechanic, { onUploadPhoto })

    const camera = screen.getByLabelText('Снять фото камерой')
    const gallery = screen.getByLabelText('Выбрать фото из файлов')
    expect(camera).toHaveAttribute('capture', 'environment')
    expect(camera).toHaveAttribute('accept', 'image/*')
    expect(gallery).not.toHaveAttribute('capture')
    expect(gallery).toHaveAttribute(
      'accept',
      'image/jpeg,image/png,image/webp,image/heic,image/heif',
    )

    const file = new File(['photo'], 'lidar.jpg', { type: 'image/jpeg' })
    fireEvent.change(gallery, { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить фото' }))

    await waitFor(() => expect(onUploadPhoto).toHaveBeenCalledWith(file))
  })
})
```

- [ ] **Step 2: Run the component test and confirm RED**

Run:

```sh
cd apps/web
npm test -- src/domains/reports/ReportDetailPanel.test.tsx
```

Expected: FAIL because `ReportDetailPanel` and its child components do not exist.

- [ ] **Step 3: Implement the reusable camera/file picker**

Create `DevicePhotoPicker.tsx`:

```tsx
import { useRef } from 'react'
import { Button } from '../../design-system/actions/Button'

export type DevicePhotoPickerProps = {
  file: File | null
  disabled?: boolean
  onFileChange: (file: File | null) => void
}

const FILE_ACCEPT = 'image/jpeg,image/png,image/webp,image/heic,image/heif'

export function DevicePhotoPicker({
  file,
  disabled = false,
  onFileChange,
}: DevicePhotoPickerProps) {
  const cameraRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const selectFirst = (files: FileList | null) => onFileChange(files?.item(0) ?? null)

  return (
    <div className="rp-photo-picker">
      <input
        accept="image/*"
        aria-label="Снять фото камерой"
        capture="environment"
        disabled={disabled}
        hidden
        onChange={(event) => selectFirst(event.currentTarget.files)}
        ref={cameraRef}
        type="file"
      />
      <input
        accept={FILE_ACCEPT}
        aria-label="Выбрать фото из файлов"
        disabled={disabled}
        hidden
        onChange={(event) => selectFirst(event.currentTarget.files)}
        ref={fileRef}
        type="file"
      />
      <div className="rp-photo-picker__actions">
        <Button
          disabled={disabled}
          leadingIcon="camera"
          onClick={() => cameraRef.current?.click()}
          type="button"
          variant="secondary"
        >
          Камера
        </Button>
        <Button
          disabled={disabled}
          leadingIcon="attachment"
          onClick={() => fileRef.current?.click()}
          type="button"
          variant="secondary"
        >
          Выбрать файл
        </Button>
      </div>
      <p aria-live="polite" className="rp-photo-picker__selection">
        {file ? `Выбрано: ${file.name}` : 'Фото не выбрано'}
      </p>
      <p className="rp-photo-picker__hint">
        JPEG, PNG, WebP или HEIC, до 15 МБ. Сам файл не сохраняется в локальном черновике.
      </p>
    </div>
  )
}
```

- [ ] **Step 4: Implement evidence list and one-slot device-photo upload**

Create `ReportAttachments.tsx`:

```tsx
import { useState } from 'react'
import { reportAttachmentHref, type Report } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { Icon } from '../../design-system/icons/Icon'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { formatBytes } from './reportModel'
import { DevicePhotoPicker } from './DevicePhotoPicker'

const kindLabel: Record<string, string> = {
  ui_snapshot: 'Снимок интерфейса',
  device_photo: 'Фото с устройства',
  client_log: 'Клиентский журнал',
}

export function ReportAttachments({
  report,
  canUploadPhoto,
  onUploadPhoto,
}: {
  report: Report
  canUploadPhoto: boolean
  onUploadPhoto: (file: File) => Promise<void>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function upload() {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      await onUploadPhoto(file)
      setFile(null)
    } catch (uploadError) {
      setError(classifyApiError(
        uploadError,
        'Не удалось прикрепить фото.',
      ).description)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="report-evidence-title" className="rp-report-evidence">
      <h3 id="report-evidence-title">Вложения</h3>
      {report.attachments.length === 0 ? (
        <p className="rp-report-muted">Вложений нет.</p>
      ) : (
        <ul className="rp-report-evidence__list">
          {report.attachments.map((attachment) => {
            const href = reportAttachmentHref(report.id, attachment.id)
            const image = attachment.content_type.startsWith('image/')
            return (
              <li className="rp-report-evidence__item" key={attachment.id}>
                {image ? (
                  <img
                    alt={`${kindLabel[attachment.kind] ?? 'Вложение'}: ${attachment.filename}`}
                    loading="lazy"
                    src={href}
                  />
                ) : (
                  <Icon aria-hidden="true" name="attachment" />
                )}
                <div>
                  <strong>{kindLabel[attachment.kind] ?? attachment.kind}</strong>
                  <span>{attachment.filename} · {formatBytes(attachment.size_bytes)}</span>
                </div>
                <a download={attachment.filename} href={href}>
                  <Icon aria-hidden="true" name="download" />
                  {`Скачать ${attachment.filename}`}
                </a>
              </li>
            )
          })}
        </ul>
      )}
      {canUploadPhoto && (
        <div className="rp-report-evidence__upload">
          <DevicePhotoPicker disabled={busy} file={file} onFileChange={setFile} />
          {error && <p role="alert">{error}</p>}
          <Button busy={busy} disabled={!file || busy} onClick={() => void upload()} type="button">
            Прикрепить фото
          </Button>
        </div>
      )}
    </section>
  )
}
```

Use the server-provided filename only as text and `download`; never interpolate it into a local filesystem path.

- [ ] **Step 5: Implement the derived timeline disclosure**

Create `ReportTimeline.tsx`:

```tsx
import type { Report } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { buildReportTimeline, formatReportDate } from './reportModel'

export function ReportTimeline({ report }: { report: Report }) {
  const events = buildReportTimeline(report)
  return (
    <section aria-labelledby="report-timeline-title" className="rp-report-timeline">
      <h3 id="report-timeline-title">Этапы по текущему состоянию</h3>
      <p className="rp-report-muted">
        Сводка построена по сохранённым полям репорта и не является полным журналом действий.
      </p>
      <ol>
        {events.map((event) => (
          <li key={event.id}>
            <StatusBadge tone={event.tone}>{event.label}</StatusBadge>
            <time dateTime={event.at ?? undefined}>{formatReportDate(event.at)}</time>
            {event.timeBasis === 'last-update' && <span>время последнего изменения</span>}
            {event.detail && <p>{event.detail}</p>}
          </li>
        ))}
      </ol>
    </section>
  )
}
```

- [ ] **Step 6: Implement only supported lifecycle actions**

Create `ReportLifecycleActions.tsx` with `mode: 'idle' | 'return' | 'escalate'`, separate comment state and these exact submit guards:

```tsx
import { useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { FormField } from '../../design-system/forms/FormField'
import { classifyApiError } from '../../shared/api/classifyApiError'
import type { ReportActionCapabilities } from './reportModel'

type Props = {
  capabilities: ReportActionCapabilities
  onReturn: (comment: string) => Promise<void>
  onComplete: () => Promise<void>
  onEscalate: (comment: string) => Promise<void>
}

export function ReportLifecycleActions({
  capabilities,
  onReturn,
  onComplete,
  onEscalate,
}: Props) {
  const [mode, setMode] = useState<'idle' | 'return' | 'escalate'>('idle')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    try {
      await action()
      setComment('')
      setMode('idle')
    } catch (actionError) {
      setError(classifyApiError(
        actionError,
        'Не удалось выполнить действие с репортом.',
      ).description)
    } finally {
      setBusy(false)
    }
  }

  if (!capabilities.canReturn
    && !capabilities.canComplete
    && !capabilities.canEscalate) return null

  return (
    <section aria-label="Действия с репортом" className="rp-report-actions">
      {error && <p role="alert">{error}</p>}
      {mode === 'idle' && (
        <div className="rp-report-actions__buttons">
          {capabilities.canReturn && (
            <Button onClick={() => setMode('return')} type="button" variant="secondary">
              Вернуть автору
            </Button>
          )}
          {capabilities.canComplete && (
            <Button busy={busy} onClick={() => void run(onComplete)} type="button">
              Завершить
            </Button>
          )}
          {capabilities.canEscalate && (
            <Button onClick={() => setMode('escalate')} type="button" variant="ghost">
              Эскалировать
            </Button>
          )}
        </div>
      )}
      {mode !== 'idle' && (
        <div className="rp-report-actions__comment">
          <FormField
            id={`report-${mode}-comment`}
            label={mode === 'return' ? 'Комментарий для автора' : 'Причина эскалации'}
            required
          >
            <textarea
              id={`report-${mode}-comment`}
              onChange={(event) => setComment(event.target.value)}
              rows={3}
              value={comment}
            />
          </FormField>
          <Button
            busy={busy}
            disabled={busy || !comment.trim()}
            onClick={() => void run(() => mode === 'return'
              ? onReturn(comment.trim())
              : onEscalate(comment.trim()))}
            type="button"
          >
            {mode === 'return' ? 'Подтвердить возврат' : 'Отправить эскалацию'}
          </Button>
          <Button disabled={busy} onClick={() => setMode('idle')} type="button" variant="ghost">
            Отмена
          </Button>
        </div>
      )}
    </section>
  )
}
```

Do not add a comment field to `done`: the current endpoint accepts none. Do not render an «assign» control.

- [ ] **Step 7: Compose the detail panel from persisted fields**

Create `ReportDetailPanel.tsx`. Its public contract must be exactly:

```tsx
import { Link } from 'react-router-dom'
import type { Report, User } from '../../api'
import { Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { reportKindLabel, reportStatusLabel } from '../../i18n/ru'
import { safeHttpUrl } from '../../lib/safeUrl'
import { ReportAttachments } from './ReportAttachments'
import { ReportLifecycleActions } from './ReportLifecycleActions'
import { ReportTimeline } from './ReportTimeline'
import {
  formatReportDate,
  reportActionsFor,
  targetRoleLabel,
} from './reportModel'

export type ReportDetailPanelProps = {
  report: Report
  user: User
  parkName?: string
  search: string
  onReturn: (comment: string) => Promise<void>
  onComplete: () => Promise<void>
  onEscalate: (comment: string) => Promise<void>
  onUploadPhoto: (file: File) => Promise<void>
}

export function ReportDetailPanel(props: ReportDetailPanelProps) {
  const { report, user, parkName, search } = props
  const capabilities = reportActionsFor(user, report)
  const trackerHref = safeHttpUrl(report.tracker_url)

  return (
    <Panel>
      <article className="rp-report-detail">
        <Link className="rp-report-detail__back" to={`/reports${search}`}>
          Назад к очереди
        </Link>
        <header>
          <div>
            <span>Репорт #{report.id}</span>
            <h2>{report.title}</h2>
          </div>
          <StatusBadge tone={report.status === 'done'
            ? 'success'
            : report.status === 'returned'
              ? 'warning'
              : 'info'}>
            {reportStatusLabel(report.status)}
          </StatusBadge>
          <span>Изменён {formatReportDate(report.updated_at)}</span>
        </header>
        <dl className="rp-report-detail__facts">
          <div><dt>Тип</dt><dd>{reportKindLabel(report.kind)}</dd></div>
          <div><dt>Парк</dt><dd>{parkName ?? (report.park_id == null ? 'Платформа' : `#${report.park_id}`)}</dd></div>
          <div><dt>Автор</dt><dd>Пользователь #{report.author_user_id}</dd></div>
          <div><dt>Адресат</dt><dd>{targetRoleLabel(report.target_role)}</dd></div>
          <div><dt>Создан</dt><dd>{formatReportDate(report.created_at)}</dd></div>
        </dl>
        {report.body && <section><h3>Описание</h3><p>{report.body}</p></section>}
        {trackerHref && (
          <a href={trackerHref} rel="noreferrer" target="_blank">
            {report.tracker_key ?? 'Открыть связанный тикет'}
          </a>
        )}
        {report.parent_report_id != null && (
          <Link to={`/reports/${report.parent_report_id}${search}`}>
            Родительский репорт #{report.parent_report_id}
          </Link>
        )}
        <ReportTimeline report={report} />
        <ReportAttachments
          canUploadPhoto={capabilities.canAttachDevicePhoto}
          onUploadPhoto={props.onUploadPhoto}
          report={report}
        />
        <ReportLifecycleActions
          capabilities={capabilities}
          onComplete={props.onComplete}
          onEscalate={props.onEscalate}
          onReturn={props.onReturn}
        />
      </article>
    </Panel>
  )
}
```

If `tracker_url` is absent, do not fabricate an external Tracker host from `tracker_key`; the API already supplies the approved URL when available.

- [ ] **Step 8: Add domain styles for the new detail primitives**

Create `reports.css` with foundation variables and the initial component rules:

```css
.rp-report-detail {
  display: grid;
  gap: var(--rp-space-6);
  min-width: 0;
}

.rp-report-detail > header,
.rp-report-detail__facts,
.rp-report-evidence__item,
.rp-report-actions__buttons,
.rp-photo-picker__actions {
  display: flex;
  gap: var(--rp-space-3);
  flex-wrap: wrap;
}

.rp-report-detail > header {
  align-items: flex-start;
  justify-content: space-between;
}

.rp-report-detail__facts {
  margin: 0;
}

.rp-report-detail__facts > div {
  min-width: min(100%, 10rem);
}

.rp-report-detail__facts dt,
.rp-report-muted,
.rp-photo-picker__hint,
.rp-photo-picker__selection {
  color: var(--rp-text-muted);
  font-size: 0.875rem;
}

.rp-report-detail__facts dd {
  margin: var(--rp-space-1) 0 0;
}

.rp-report-evidence__list,
.rp-report-timeline ol {
  display: grid;
  gap: var(--rp-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.rp-report-evidence__item {
  align-items: center;
  padding: var(--rp-space-3);
  border: 1px solid var(--rp-border);
  border-radius: var(--rp-radius-panel);
}

.rp-report-evidence__item img {
  width: 5rem;
  height: 4rem;
  object-fit: cover;
  border-radius: var(--rp-radius-control);
}

.rp-report-evidence__item > div {
  display: grid;
  flex: 1 1 12rem;
  min-width: 0;
}

.rp-report-actions textarea,
.rp-report-evidence input {
  font-size: 1rem;
}

@media (max-width: 599px) {
  .rp-report-detail > header,
  .rp-report-detail__facts,
  .rp-report-evidence__item {
    align-items: stretch;
    flex-direction: column;
  }

  .rp-report-evidence__item img {
    width: 100%;
    height: auto;
    max-height: 16rem;
  }

  .rp-report-actions__buttons,
  .rp-photo-picker__actions {
    display: grid;
    grid-template-columns: 1fr;
  }
}
```

These are the frozen Phase 1 semantic tokens; do not add raw light/dark colors.

- [ ] **Step 9: Run GREEN and component regressions**

Run:

```sh
cd apps/web
npm test -- \
  src/domains/reports/reportModel.test.ts \
  src/domains/reports/ReportDetailPanel.test.tsx
npm run lint
npm run build
git diff --check
```

Expected: detail tests pass; camera and file inputs are distinct; all mutation errors are announced; no SLA/assignee UI exists.

- [ ] **Step 10: Commit**

```sh
git add \
  apps/web/src/domains/reports/DevicePhotoPicker.tsx \
  apps/web/src/domains/reports/ReportAttachments.tsx \
  apps/web/src/domains/reports/ReportTimeline.tsx \
  apps/web/src/domains/reports/ReportLifecycleActions.tsx \
  apps/web/src/domains/reports/ReportDetailPanel.tsx \
  apps/web/src/domains/reports/ReportDetailPanel.test.tsx \
  apps/web/src/domains/reports/reports.css \
  apps/web/src/i18n/ru.ts
git commit -m "feat(web): add report lifecycle and evidence detail"
```

---

### Task 4: Add the inbox-first list/detail workspace and canonical routes

**Files:**
- Create: `apps/web/src/domains/reports/ReportQueue.tsx`
- Create: `apps/web/src/domains/reports/ReportFiltersBar.tsx`
- Create: `apps/web/src/domains/reports/ReportWorkspacePage.tsx`
- Create: `apps/web/src/domains/reports/ReportWorkspacePage.test.tsx`
- Modify: `apps/web/src/domains/reports/reports.css`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Replace: `apps/web/src/pages/Reports.tsx:1-208`
- Modify: `apps/web/src/i18n/ru.ts:330-352`

**Interfaces:**
- Consumes: Task 1 API helpers, Task 2 filters/capabilities, Task 3 `ReportDetailPanel`, foundation `useParkScope`, `PageLayout`, `Panel`, `LoadingState`, `EmptyState`, `ErrorState`, `Button`, and Phase 2 `classifyApiError`.
- Produces: `ReportQueue`, `ReportFiltersBar`, `ReportWorkspacePage`; route ids `'reports' | 'report-detail'`; canonical `/reports` and `/reports/:reportId`.
- Security invariant: every persisted list/detail key includes `user.id`. Only `offline`, `timeout`, and `server` revalidation failures may retain protected cached report payload; `unauthorized`/`forbidden` synchronously suppress and evict the current user's report cache, unmount lifecycle actions, and invoke fail-closed auth refresh.

- [ ] **Step 1: Write failing queue and workspace tests**

Create `ReportWorkspacePage.test.tsx`. Use `vi.hoisted` for the foundation park hook and the real `AuthContext.Provider`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Report, type User } from '../../api'
import { AuthContext, type AuthContextValue } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { ReportWorkspacePage } from './ReportWorkspacePage'

const parkHook = vi.hoisted(() => ({
  value: {
    parkId: 7,
    selectedPark: { id: 7, name: 'Север', tag: 'NORTH' },
    parks: [{ id: 7, name: 'Север', tag: 'NORTH' }],
    loading: false,
    locked: false,
    setParkId: vi.fn(),
    refreshParks: vi.fn().mockResolvedValue(undefined),
  },
}))

vi.mock('../../app/park/parkScope', () => ({
  useParkScope: () => parkHook.value,
}))

const report: Report = {
  id: 41,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 3,
  target_role: 'operator',
  tracker_key: null,
  tracker_url: null,
  title: 'Не включается лидар',
  body: 'После перезапуска ошибка остаётся.',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-01T07:00:00Z',
  updated_at: '2026-09-01T07:00:00Z',
  resolved_at: null,
  attachments: [],
}

const operator: User = {
  id: 2,
  username: 'operator-test',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create', 'reports.resolve'],
  parks: [{ id: 7, name: 'Север', tag: 'NORTH' }],
}

function renderRoute(user: User, entry: string) {
  const auth: AuthContextValue = {
    user,
    loading: false,
    login: vi.fn(),
    refreshUser: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AuthContext.Provider value={auth}>
        <Routes>
          <Route path="/reports" element={<ReportWorkspacePage />} />
          <Route path="/reports/:reportId" element={<ReportWorkspacePage />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  resourceStore.clearAll()
  vi.restoreAllMocks()
})

describe('ReportWorkspacePage', () => {
  it('opens resolver inbox first and restores a detail deep link', async () => {
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([report])
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])
    vi.spyOn(api, 'report').mockResolvedValue(report)

    renderRoute(
      operator,
      '/reports/41?view=inbox&status=all&age=all&kind=all&park=7',
    )

    await waitFor(() => expect(api.reportsInbox).toHaveBeenCalledWith(7))
    await waitFor(() => expect(api.report).toHaveBeenCalledWith(41))
    expect(screen.getByRole('button', { name: 'Входящие' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getAllByText('Не включается лидар')).toHaveLength(2)
    expect(screen.getByRole('link', { name: /Не включается лидар/i })).toHaveAttribute(
      'href',
      '/reports/41?view=inbox&status=all&age=all&kind=all&park=7',
    )
  })

  it('shows only supported inbox filters', async () => {
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([report])
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])

    renderRoute(operator, '/reports?park=7')

    await screen.findByRole('link', { name: /Не включается лидар/i })
    expect(screen.getByLabelText('Возраст')).toBeInTheDocument()
    expect(screen.getByLabelText('Тип репорта')).toBeInTheDocument()
    expect(screen.queryByLabelText('Статус')).not.toBeInTheDocument()
    expect(screen.queryByText(/SLA/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Ответственный/i)).not.toBeInTheDocument()
  })
})
```

Also add to `routeManifest.test.ts`:

```ts
it('registers report list and detail without duplicate nav entries', () => {
  const list = ROUTE_MANIFEST.find((route) => route.id === 'reports')
  const detail = ROUTE_MANIFEST.find((route) => route.id === 'report-detail')

  expect(list).toMatchObject({
    path: '/reports',
    permission: 'nav.reports',
    surface: 'shell',
    nav: { group: 'collaboration', desktopOrder: 10 },
  })
  expect(detail).toMatchObject({
    path: '/reports/:reportId',
    permission: 'nav.reports',
    surface: 'shell',
  })
  expect(detail?.nav).toBeUndefined()
  expect(ROUTE_MANIFEST.filter((route) => route.path === '/reports')).toHaveLength(1)
})
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```sh
cd apps/web
npm test -- \
  src/domains/reports/ReportWorkspacePage.test.tsx \
  src/app/routing/routeManifest.test.ts
```

Expected: FAIL because the workspace components and the two report route ids are absent.

- [ ] **Step 3: Implement the semantic queue**

Create `ReportQueue.tsx`:

```tsx
import { Link } from 'react-router-dom'
import type { Park, Report } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { reportKindLabel, reportStatusLabel } from '../../i18n/ru'
import { formatReportDate } from './reportModel'

export function ReportQueue({
  reports,
  parks,
  selectedId,
  search,
}: {
  reports: readonly Report[]
  parks: readonly Park[]
  selectedId: number | null
  search: string
}) {
  const parkNames = new Map(parks.map((park) => [park.id, park.name]))
  return (
    <ol aria-label="Очередь репортов" className="rp-report-queue">
      {reports.map((report) => (
        <li key={report.id}>
          <Link
            aria-current={selectedId === report.id ? 'page' : undefined}
            className="rp-report-queue__item"
            to={`/reports/${report.id}${search}`}
          >
            <span className="rp-report-queue__heading">
              <strong>{report.title}</strong>
              <StatusBadge tone={report.status === 'done'
                ? 'success'
                : report.status === 'returned'
                  ? 'warning'
                  : 'info'}>
                {reportStatusLabel(report.status)}
              </StatusBadge>
            </span>
            <span>{reportKindLabel(report.kind)}</span>
            <span>{report.park_id == null
              ? 'Платформа'
              : parkNames.get(report.park_id) ?? `Парк #${report.park_id}`}</span>
            <time dateTime={report.created_at}>{formatReportDate(report.created_at)}</time>
          </Link>
        </li>
      ))}
    </ol>
  )
}
```

- [ ] **Step 4: Implement filters while delegating park URL state to foundation**

Create `ReportFiltersBar.tsx` with this public interface and controls:

```tsx
import type { AccessUser } from '../../app/routing/accessPolicy'
import type { Park } from '../../api'
import { FormField } from '../../design-system/forms/FormField'
import type { ReportFilterState, ReportQueueView } from './reportModel'

export function ReportFiltersBar({
  filters,
  user,
  parks,
  onChange,
  onParkChange,
}: {
  filters: ReportFilterState
  user: AccessUser
  parks: readonly Park[]
  onChange: (next: ReportFilterState) => void
  onParkChange: (parkId: number) => void
}) {
  const views: ReportQueueView[] = (user.permissions ?? []).includes('reports.resolve')
    ? ['inbox', 'mine']
    : ['mine']
  return (
    <div className="rp-report-filters">
      <div aria-label="Источник репортов" className="rp-report-filters__views">
        {views.map((view) => (
          <button
            aria-pressed={filters.view === view}
            key={view}
            onClick={() => onChange({ ...filters, view, status: 'all' })}
            type="button"
          >
            {view === 'inbox' ? 'Входящие' : 'Мои'}
          </button>
        ))}
      </div>
      {filters.view === 'mine' && (
        <FormField id="report-status" label="Статус">
          <select
            id="report-status"
            onChange={(event) => onChange({
              ...filters,
              status: event.target.value as ReportFilterState['status'],
            })}
            value={filters.status}
          >
            <option value="all">Все</option>
            <option value="open">Открытые</option>
            <option value="returned">Возвращённые</option>
            <option value="done">Завершённые</option>
          </select>
        </FormField>
      )}
      <FormField id="report-age" label="Возраст">
        <select
          id="report-age"
          onChange={(event) => onChange({
            ...filters,
            age: event.target.value as ReportFilterState['age'],
          })}
          value={filters.age}
        >
          <option value="all">Любой</option>
          <option value="24h">До суток</option>
          <option value="72h">От суток до трёх</option>
          <option value="older">Старше трёх суток</option>
        </select>
      </FormField>
      <FormField id="report-kind" label="Тип репорта">
        <select
          id="report-kind"
          onChange={(event) => onChange({
            ...filters,
            kind: event.target.value as ReportFilterState['kind'],
          })}
          value={filters.kind}
        >
          <option value="all">Все типы</option>
          <option value="ticket_question">Вопрос по тикету</option>
          <option value="ticket_close_review">Проверка закрытия</option>
          <option value="mechanic_problem">Проблема</option>
          <option value="escalation_to_admin">Эскалация</option>
          <option value="emergency_cookie_stale">Системная проблема</option>
        </select>
      </FormField>
      {parks.length > 1 && (
        <FormField id="report-park" label="Парк">
          <select
            id="report-park"
            onChange={(event) => onParkChange(Number(event.target.value))}
            value={filters.parkId ?? ''}
          >
            {parks.map((park) => <option key={park.id} value={park.id}>{park.name}</option>)}
          </select>
        </FormField>
      )}
    </div>
  )
}
```

Do not add an «all parks» value: foundation `ParkScopeProvider` owns one explicit selected park and keeps it in `?park=`.

- [ ] **Step 5: Implement the workspace controller with unconditional hooks**

Create `ReportWorkspacePage.tsx`; import `./reports.css`. Keep all three resource hooks unconditional and enable only the selected source:

```tsx
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type Report } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { refreshReportsBadge } from '../../reports-badge'
import { ReportDetailPanel } from './ReportDetailPanel'
import { ReportFiltersBar } from './ReportFiltersBar'
import { ReportQueue } from './ReportQueue'
import {
  canCreateReport,
  filterReportQueue,
  parseReportFilters,
  serializeReportFilters,
} from './reportModel'
import './reports.css'

const anonymousUser = {
  id: -1,
  role: 'driver',
  access_status: 'pending',
  must_change_password: false,
  permissions: [],
  parks: [],
} as const

export function ReportWorkspacePage() {
  const { user, refreshUser } = useAuth()
  const parkScope = useParkScope()
  const location = useLocation()
  const navigate = useNavigate()
  const { reportId: rawReportId } = useParams()
  const accessUser = user ?? anonymousUser
  const filters = parseReportFilters(location.search, {
    user: accessUser,
    parkId: parkScope.parkId,
  })
  const reportId = rawReportId == null ? null : Number(rawReportId)
  const validReportId = reportId == null || (Number.isInteger(reportId) && reportId > 0)
  const inboxEnabled = Boolean(user)
    && filters.view === 'inbox'
    && (user?.permissions ?? []).includes('reports.resolve')
    && parkScope.parkId != null
  const mineEnabled = Boolean(user) && filters.view === 'mine'

  const inbox = useCachedResource<Report[]>(
    inboxEnabled ? `reports:inbox:${user?.id}:${parkScope.parkId}` : '',
    () => api.reportsInbox(parkScope.parkId ?? undefined),
    { enabled: inboxEnabled },
  )
  const mine = useCachedResource<Report[]>(
    mineEnabled ? `reports:mine:${user?.id}` : '',
    () => api.reportsMine(),
    { enabled: mineEnabled },
  )
  const detail = useCachedResource<Report>(
    validReportId && reportId != null && user ? `reports:detail:${user.id}:${reportId}` : '',
    () => api.report(reportId as number),
    { enabled: Boolean(user) && validReportId && reportId != null },
  )

  const active = filters.view === 'inbox' ? inbox : mine
  const visibleReports = filterReportQueue(active.data ?? [], filters)
  const hasListData = active.data !== undefined
  const listError = active.error
    ? classifyApiError(active.error, 'Не удалось загрузить репорты.')
    : null
  const detailError = detail.error
    ? classifyApiError(detail.error, 'Не удалось открыть репорт.')
    : null
  const canRetainProtectedData = (failure: DomainError | null) =>
    failure != null && ['offline', 'timeout', 'server'].includes(failure.kind)
  const listCanRenderData = hasListData
    && (!listError || canRetainProtectedData(listError))
  const detailCanRenderData = detail.data !== undefined
    && (!detailError || canRetainProtectedData(detailError))
  const authorizationFailure = (
    listError?.kind === 'unauthorized' || listError?.kind === 'forbidden'
  )
    ? active.error
    : (
        detailError?.kind === 'unauthorized' || detailError?.kind === 'forbidden'
      )
      ? detail.error
      : null
  const canonicalSearch = serializeReportFilters(filters, location.search)

  useEffect(() => {
    if (!authorizationFailure) return
    resourceStore.invalidate(`reports:inbox:${accessUser.id}:`, { prefix: true })
    resourceStore.invalidate(`reports:mine:${accessUser.id}`, { prefix: true })
    resourceStore.invalidate(`reports:detail:${accessUser.id}:`, { prefix: true })
    void refreshUser().catch(() => undefined)
  }, [accessUser.id, authorizationFailure, refreshUser])

  if (!user) return <LoadingState label="Загрузка репортов" variant="page" />

  function changeFilters(next: typeof filters) {
    navigate({ pathname: location.pathname, search: serializeReportFilters(next, location.search) }, { replace: true })
  }

  async function acceptMutation(updated: Report) {
    resourceStore.set(`reports:detail:${user.id}:${updated.id}`, updated, true)
    resourceStore.invalidate(`reports:inbox:${user.id}:`, { prefix: true })
    resourceStore.invalidate(`reports:mine:${user.id}`, { prefix: true })
    await active.refresh()
    refreshReportsBadge()
  }

  async function uploadPhoto(file: File) {
    if (reportId == null) return
    await api.addReportAttachment(reportId, 'device_photo', file)
    resourceStore.invalidate(`reports:detail:${user.id}:${reportId}`)
    await detail.refresh()
  }

  return (
    <PageLayout
      actions={canCreateReport(user) ? (
        <Button
          leadingIcon="send"
          onClick={() => navigate(`/reports/new${canonicalSearch}`)}
          type="button"
        >
          Новый репорт
        </Button>
      ) : undefined}
      description="Входящие решения и ваши обращения в одном рабочем пространстве."
      title="Репорты"
    >
      <ReportFiltersBar
        filters={filters}
        onChange={changeFilters}
        onParkChange={(id) => parkScope.setParkId(id, { replace: true })}
        parks={parkScope.parks}
        user={user}
      />
      <div className="rp-report-workspace" data-has-detail={reportId != null}>
        <Panel title={filters.view === 'inbox' ? 'Входящие' : 'Мои репорты'}>
          {active.isLoading && <LoadingState label="Загрузка очереди" variant="panel" />}
          {listError && (!hasListData || !listCanRenderData) && (
            <ErrorState
              description={listError.description}
              onRetry={() => void active.refresh()}
              requestId={listError.requestId}
              title={listError.title}
            />
          )}
          {listError && listCanRenderData && (
            <div className="rp-report-stale-warning" role="alert">
              <StaleBadge state={listError.kind === 'offline' ? 'offline' : 'stale'} />
              <span>{listError.description}</span>
              {listError.retryable && <Button onClick={() => void active.refresh()} variant="secondary">Повторить</Button>}
            </div>
          )}
          {!active.isLoading && listCanRenderData && visibleReports.length === 0 && (
            <EmptyState
              description="Измените доступные фильтры или создайте новый репорт."
              icon="reports"
              title="В этой очереди репортов нет"
            />
          )}
          {!active.isLoading && listCanRenderData && visibleReports.length > 0 && (
            <ReportQueue
              parks={parkScope.parks}
              reports={visibleReports}
              search={canonicalSearch}
              selectedId={reportId}
            />
          )}
        </Panel>
        <div className="rp-report-workspace__detail">
          {!validReportId && (
            <ErrorState
              description="В адресе должен быть положительный числовой идентификатор."
              title="Некорректный номер репорта"
            />
          )}
          {validReportId && reportId == null && (
            <EmptyState
              description="На широком экране карточка откроется рядом; на телефоне — отдельным экраном."
              icon="reports"
              title="Выберите репорт из очереди"
            />
          )}
          {validReportId && reportId != null && detail.isLoading && (
            <LoadingState label="Загрузка репорта" variant="panel" />
          )}
          {validReportId && reportId != null && detailError && !detailCanRenderData && (
            <ErrorState
              description={detailError.description}
              onRetry={() => void detail.refresh()}
              requestId={detailError.requestId}
              title={detailError.title}
            />
          )}
          {validReportId && reportId != null && detailError && detailCanRenderData && (
            <div className="rp-report-stale-warning" role="alert">
              <StaleBadge state={detailError.kind === 'offline' ? 'offline' : 'stale'} />
              <span>{detailError.description}</span>
              {detailError.retryable && <Button onClick={() => void detail.refresh()} variant="secondary">Повторить</Button>}
            </div>
          )}
          {validReportId && reportId != null && detailCanRenderData && (
            <ReportDetailPanel
              onComplete={async () => acceptMutation(await api.reportDone(reportId))}
              onEscalate={async (comment) => {
                const child = await api.reportEscalate(reportId, comment)
                await acceptMutation(child)
                navigate(`/reports/${child.id}${canonicalSearch}`, { replace: true })
              }}
              onReturn={async (comment) => acceptMutation(await api.reportReturn(reportId, comment))}
              onUploadPhoto={uploadPhoto}
              parkName={parkScope.parks.find((park) => park.id === detail.data?.park_id)?.name}
              report={detail.data}
              search={canonicalSearch}
              user={user}
            />
          )}
        </div>
      </div>
    </PageLayout>
  )
}
```

Import `StaleBadge`, `DomainError`, and `useEffect`. Apply the same allowlisted data-preserving condition independently to list and detail. An offline/timeout/server error with cached data renders a compact classified alert plus stale badge while the last payload stays mounted; every other kind renders `ErrorState` and no cached payload/actions. Derive `authorizationFailure` as the stable raw resource error behind a classified `unauthorized`/`forbidden` list or detail failure. In an effect keyed by that raw error object, invalidate `reports:inbox:${user.id}:`, `reports:mine:${user.id}`, and `reports:detail:${user.id}:` by prefix and call `refreshUser()` exactly once for that observed denial. Render suppression comes from `listCanRenderData`/`detailCanRenderData` immediately and never waits for the effect. Keep all resource hooks above every return so switching role/view cannot change hook order. `acceptMutation` keeps completed/returned detail visible even after it leaves the inbox.

Add a `ReportWorkspacePage.test.tsx` regression that seeds the current user's scoped list and selected-detail records, rejects both revalidations with offline/server failures, and asserts the cached queue/detail plus two localized non-blocking alerts remain. A separate cold-load rejection must show the full error state and no invented queue. Add seeded-cache `401` and `403` cases: after the denial resolves, report title/body/attachments/actions are absent, current-user report keys are gone, another synthetic user's same-ID detail remains untouched, full classified error is visible, and `refreshUser` ran exactly once. Clear the shared store and coalescing map after every test.

- [ ] **Step 6: Replace the Foundation Reports entry and add only its detail route ID**

Foundation already defines and registers `reports`. Extend `AppRouteId` only with:

```ts
  | 'report-detail'
```

Replace the existing `reports` manifest record in place and add the `report-detail` record; do not create a second `reports` item:

```ts
  {
    id: 'reports',
    path: '/reports',
    label: 'Репорты',
    icon: 'reports',
    permission: 'nav.reports',
    prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
    surface: 'shell',
    nav: {
      group: 'collaboration',
      desktopOrder: 10,
      mobilePriority: { mechanic: 30, operator: 30, admin: 40, royal: 40 },
    },
  },
  {
    id: 'report-detail',
    path: '/reports/:reportId',
    label: 'Репорт',
    icon: 'reports',
    permission: 'nav.reports',
    prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
    surface: 'shell',
  },
```

Import `ReportWorkspacePage` in `AppRouter.tsx` and extend `ROUTE_ELEMENTS`:

```tsx
  reports: <ReportWorkspacePage />,
  'report-detail': <ReportWorkspacePage />,
```

Replace `apps/web/src/pages/Reports.tsx` with a compatibility adapter:

```ts
export { ReportWorkspacePage as Reports } from '../domains/reports/ReportWorkspacePage'
```

- [ ] **Step 7: Complete responsive workspace styles**

Append to `reports.css`:

```css
.rp-report-filters {
  display: flex;
  gap: var(--rp-space-3);
  align-items: end;
  flex-wrap: wrap;
  margin-block-end: var(--rp-space-4);
}

.rp-report-filters__views {
  display: inline-flex;
  gap: var(--rp-space-1);
}

.rp-report-filters__views button,
.rp-report-queue__item {
  min-height: 2.75rem;
}

.rp-report-filters__views button[aria-pressed='true'] {
  color: var(--rp-action-on);
  background: var(--rp-action);
}

.rp-report-workspace {
  display: grid;
  gap: var(--rp-space-4);
  min-width: 0;
}

.rp-report-queue {
  display: grid;
  gap: var(--rp-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.rp-report-queue__item {
  display: grid;
  gap: var(--rp-space-2);
  padding: var(--rp-space-3);
  color: var(--rp-text);
  text-decoration: none;
  border: 1px solid var(--rp-border);
  border-radius: var(--rp-radius-panel);
  background: var(--rp-surface);
}

.rp-report-queue__item[aria-current='page'] {
  border-color: var(--rp-action);
  box-shadow: inset 0.1875rem 0 0 var(--rp-action);
}

.rp-report-queue__heading {
  display: flex;
  gap: var(--rp-space-2);
  align-items: flex-start;
  justify-content: space-between;
}

.rp-report-stale-warning {
  display: flex;
  gap: var(--rp-space-2);
  align-items: center;
  flex-wrap: wrap;
  margin-block-end: var(--rp-space-3);
  padding: var(--rp-space-3);
  color: var(--rp-warning);
  border: 1px solid currentColor;
  border-radius: var(--rp-radius-control);
  background: var(--rp-warning-surface);
}

.rp-report-stale-warning > span:not([class]) {
  flex: 1 1 14rem;
}

@media (min-width: 900px) {
  .rp-report-workspace {
    grid-template-columns: minmax(18rem, 24rem) minmax(0, 1fr);
    align-items: start;
  }

  .rp-report-workspace__detail {
    position: sticky;
    top: var(--rp-space-4);
    min-width: 0;
    max-height: calc(100dvh - var(--rp-space-8));
    overflow: auto;
  }
}

@media (max-width: 899px) {
  .rp-report-workspace[data-has-detail='true'] > :first-child {
    display: none;
  }

  .rp-report-workspace[data-has-detail='false'] .rp-report-workspace__detail {
    display: none;
  }
}

@media (max-width: 599px) {
  .rp-report-filters,
  .rp-report-filters > * {
    width: 100%;
  }

  .rp-report-filters select,
  .rp-report-filters__views {
    width: 100%;
    font-size: 1rem;
  }

  .rp-report-filters__views button {
    flex: 1;
  }
}
```

Insert this independent row into the cumulative `protectedRoutes` literal in `accessPolicy.test.ts` before its `] as const`:

```ts
{ id: 'report-detail', permission: 'nav.reports', operatorOnly: false, mechanicPark: true },
```

Keep the existing `reports` row and all Cartesian loops. They now prove both report IDs across system/custom roles, approved/pending/rejected status, permission present/absent, forced password change, and mechanic park present/absent. The expectation remains independent from `ROUTE_MANIFEST`.

- [ ] **Step 8: Run GREEN, nav parity, and workspace regressions**

Run:

```sh
cd apps/web
npm test -- \
  src/domains/reports/reportModel.test.ts \
  src/domains/reports/ReportDetailPanel.test.tsx \
  src/domains/reports/ReportWorkspacePage.test.tsx \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts
npm run check-nav
npm run lint
npm run build
git diff --check
```

Expected: list/detail deep links compile and pass; exactly `/reports` appears in navigation; `/reports/:reportId` is guarded but not duplicated in nav.

- [ ] **Step 9: Commit**

```sh
git add \
  apps/web/src/domains/reports/ReportQueue.tsx \
  apps/web/src/domains/reports/ReportFiltersBar.tsx \
  apps/web/src/domains/reports/ReportWorkspacePage.tsx \
  apps/web/src/domains/reports/ReportWorkspacePage.test.tsx \
  apps/web/src/domains/reports/reports.css \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/app/routing/routeManifest.test.ts \
  apps/web/src/app/routing/accessPolicy.test.ts \
  apps/web/src/pages/Reports.tsx \
  apps/web/src/i18n/ru.ts
git commit -m "feat(web): add inbox-first report workspace"
```

---

### Task 5: Add the recoverable mobile composer and canonical new route

**Files:**
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/src/robopark_api/routers/reports.py`
- Create: `apps/api/alembic/versions/0016_report_idempotency.py`
- Modify: `apps/api/tests/test_reports.py`
- Modify: `apps/api/tests/test_models_migration.py`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/api.report-create.test.ts`
- Create: `apps/web/src/domains/reports/reportDraft.ts`
- Create: `apps/web/src/domains/reports/reportDraft.test.ts`
- Consume unchanged: `apps/web/src/shared/auth/protectedBrowserStorage.ts`
- Create: `apps/web/src/domains/reports/ReportComposerPage.tsx`
- Create: `apps/web/src/domains/reports/ReportComposerPage.test.tsx`
- Modify: `apps/web/src/domains/reports/reports.css`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/AppRouter.test.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`

**Interfaces:**
- Consumes: `ReportKindManual`, `ReportCreatePayload`, `api.createReport(payload, idempotencyKey)`, `api.addReportAttachment`, `useParkScope`, `canCreateReport`, Plan 02 `REPORT_DRAFT_STORAGE_PREFIX` / `clearProtectedBrowserStorage`, and foundation form/action/feedback primitives.
- Produces: `ReportDraft`, `REPORT_DRAFT_MAX_AGE_MS`, `reportDraftStorageKey`, `emptyReportDraft`, `loadReportDraft`, `saveReportDraft`, `clearReportDraft`, `ReportComposerPage`.
- Persistence invariant: the live-session storage key includes numeric user id, but ID scoping is not treated as durable identity isolation. The JSON payload is versioned and expires after seven days; Plan 02 prefix-wide cleanup removes every draft before login and on logout/refresh-401, including when a deleted account's numeric ID is reused. It never contains `File`, Blob content, object URLs, credentials, or API responses. Successful reload and transient offline/timeout/server failures retain the same-session draft.
- Retry invariant: generate and persist one opaque `idempotencyKey` before the first `POST /reports`. An ambiguous offline/timeout/5xx response keeps that same key and locks the submitted fields; retry sends the identical payload/key and the server returns the one existing/new report. Once a response supplies `createdReportId`, persist it synchronously before photo upload and skip all later create calls.

- [ ] **Step 1: Prove report creation needs durable idempotency**

Extend `apps/api/tests/test_reports.py` with HTTP-level cases using a syntactically valid synthetic key:

1. send the same author, identical `ReportCreateIn`, and `Idempotency-Key: report-create-00000000-0000-4000-8000-000000000041` twice; both responses return report `id=41` and the database contains exactly one matching manual report;
2. reuse the same author/key with a changed title and receive `409 {"detail":"idempotency_key_reused"}` without a second row;
3. use the same key for another approved author and receive a distinct row, proving keys are scoped by author and reveal nothing cross-user;
4. omit the header twice and preserve legacy behavior: two ordinary manual reports are created;
5. exercise the service's unique-constraint race recovery by forcing the pre-read to miss and the insert to collide, then assert it rolls back, reloads the matching row and returns it only when the full canonical create payload matches.

Extend `test_models_migration.py` to upgrade an `0015_report_attachments` database through the exact revision `0016_report_idempotency`, assert nullable `reports.client_request_id` exists, and assert the unique `(author_user_id, client_request_id)` index rejects a duplicate non-null pair while allowing legacy null rows. The test also asserts that this phase leaves exactly one Alembic head with that exact ID.

Create `apps/web/src/api.report-create.test.ts`: call `api.createReport(payload, key)` and assert exact JSON plus `Idempotency-Key`; call the legacy optional form without a key and assert that header is absent. No test uses a production-looking token.

Run:

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_reports.py tests/test_models_migration.py
uv run --frozen alembic heads
cd ../web
npm test -- src/api.report-create.test.ts
```

Expected: FAIL because the column/index/migration, service deduplication, optional router header, and client transport parameter do not exist.

- [ ] **Step 2: Add the backward-compatible server idempotency contract**

Create `0016_report_idempotency.py` with this exact Alembic identity; do not accept a generated/random revision ID:

```python
"""Durable idempotency for manual report creation.

Revision ID: 0016_report_idempotency
Revises: 0015_report_attachments
"""

from collections.abc import Sequence

revision: str = "0016_report_idempotency"
down_revision: str | None = "0015_report_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
```

Add nullable `String(64)` column `reports.client_request_id` and a unique index named `uq_reports_author_client_request_id` over `author_user_id, client_request_id`; downgrade drops the index before the column. Mirror the index and nullable field in `Report.__table_args__`/model. Existing automatic reports and legacy clients keep `NULL`, so their semantics do not change and multiple null rows remain valid on SQLite/PostgreSQL.

In `routers/reports.py`, accept optional `Idempotency-Key` only on `POST /reports` using an `Annotated` `Header` constrained to `16..64` ASCII characters matching `^[A-Za-z0-9_-]+$`. Pass it to `create_manual_report`; authorization and `_require_mechanic_park` still run before lookup, and `ReportOut` does not expose the key. Map a dedicated `ReportIdempotencyConflict` before the generic `ValueError` branch to `409 idempotency_key_reused`.

In `services/reports.py`, let `create_manual_report(..., client_request_id: str | None = None)` first query by the exact `(author.id, client_request_id)` when non-null. Return an existing row only if `kind`, `park_id`, normalized title/body, `tracker_key`, and `tracker_url` all equal the canonical incoming payload; otherwise raise `ReportIdempotencyConflict`. Store the key on a new row. Catch only the named unique-index `IntegrityError` race around commit, roll back, reload the pair, apply the same full-payload comparison, and either return that row or raise the conflict; unrelated integrity errors still propagate. This is persistence-backed deduplication, not an in-memory lock.

In `api.ts`, change the signature to `createReport(payload, idempotencyKey?: string)` and add the exact header only when supplied. Run the RED commands again plus Ruff on the touched backend files; expected: GREEN, one row survives a same-key replay, and all legacy create tests remain unchanged.

- [ ] **Step 3: Write failing draft persistence tests**

Create `apps/web/src/domains/reports/reportDraft.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'vitest'
import { clearProtectedBrowserStorage } from '../../shared/auth/protectedBrowserStorage'
import {
  REPORT_DRAFT_MAX_AGE_MS,
  clearReportDraft,
  emptyReportDraft,
  loadReportDraft,
  reportDraftStorageKey,
  saveReportDraft,
} from './reportDraft'

beforeEach(() => {
  localStorage.clear()
})

describe('report draft persistence', () => {
  it('round-trips only the current user draft', () => {
    const draft = {
      ...emptyReportDraft(7),
      kind: 'mechanic_problem' as const,
      title: 'Не включается лидар',
      body: 'Ошибка остаётся после перезапуска.',
    }
    saveReportDraft(3, draft, 1_000, localStorage)

    expect(loadReportDraft(3, 1_001, localStorage)).toEqual(draft)
    expect(loadReportDraft(4, 1_001, localStorage)).toBeNull()
    expect(localStorage.getItem(reportDraftStorageKey(3))).not.toContain('photo')
    expect(draft.idempotencyKey).toMatch(/^[A-Za-z0-9_-]{16,64}$/)
  })

  it('removes expired and corrupt values instead of restoring them', () => {
    saveReportDraft(3, emptyReportDraft(7), 1_000, localStorage)
    expect(loadReportDraft(
      3,
      1_000 + REPORT_DRAFT_MAX_AGE_MS + 1,
      localStorage,
    )).toBeNull()
    expect(localStorage.getItem(reportDraftStorageKey(3))).toBeNull()

    localStorage.setItem(reportDraftStorageKey(3), '{"version":1,"title":')
    expect(loadReportDraft(3, 2_000, localStorage)).toBeNull()
    expect(localStorage.getItem(reportDraftStorageKey(3))).toBeNull()
  })

  it('preserves a created report id until attachment completion', () => {
    const draft = { ...emptyReportDraft(7), createdReportId: 41 }
    saveReportDraft(3, draft, 1_000, localStorage)
    expect(loadReportDraft(3, 1_001, localStorage)?.createdReportId).toBe(41)

    clearReportDraft(3, localStorage)
    expect(loadReportDraft(3, 1_001, localStorage)).toBeNull()
  })

  it('persists an ambiguous create checkpoint with the same opaque key', () => {
    const draft = {
      ...emptyReportDraft(7, 'report-create-00000000-0000-4000-8000-000000000041'),
      title: 'Не включается лидар',
      createAttempted: true,
    }
    saveReportDraft(3, draft, 1_000, localStorage)
    expect(loadReportDraft(3, 1_001, localStorage)).toEqual(draft)
  })

  it('never restores an old draft when a deleted account id is reused', () => {
    const stale = { ...emptyReportDraft(7), title: 'Draft from deleted account' }
    saveReportDraft(3, stale, 1_000, localStorage)
    localStorage.setItem(
      'robopark.recentRobots.v2.3',
      JSON.stringify([{ query: '447', vin: 'YASADR00000000447', openedAt: 1_000 }]),
    )

    // Auth session boundary happens before a replacement account id=3 can render.
    clearProtectedBrowserStorage(localStorage)

    expect(loadReportDraft(3, 1_001, localStorage)).toBeNull()
    expect(localStorage.getItem('robopark.recentRobots.v2.3')).toBeNull()
  })

  it('keeps the same-session draft across ordinary reload and offline recovery', () => {
    const draft = { ...emptyReportDraft(7), title: 'Continue after reconnect' }
    saveReportDraft(3, draft, 1_000, localStorage)

    expect(loadReportDraft(3, 1_001, localStorage)).toEqual(draft)
    expect(loadReportDraft(3, 2_000, localStorage)).toEqual(draft)
  })
})
```

- [ ] **Step 4: Run the draft test and confirm RED**

```sh
cd apps/web
npm test -- src/domains/reports/reportDraft.test.ts
```

Expected: FAIL because `reportDraft.ts` does not exist.

- [ ] **Step 5: Implement a validated, versioned, seven-day draft**

Create `reportDraft.ts`:

```ts
import type { ReportKindManual } from '../../api'
import { REPORT_DRAFT_STORAGE_PREFIX } from '../../shared/auth/protectedBrowserStorage'

export const REPORT_DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
const REPORT_DRAFT_VERSION = 1

export type ReportDraft = {
  kind: ReportKindManual
  parkId: number | null
  trackerKey: string
  title: string
  body: string
  idempotencyKey: string
  createAttempted: boolean
  createdReportId: number | null
}

type StoredReportDraft = ReportDraft & {
  version: typeof REPORT_DRAFT_VERSION
  userId: number
  savedAt: number
}

export function reportDraftStorageKey(userId: number): string {
  return `${REPORT_DRAFT_STORAGE_PREFIX}v1:${userId}`
}

export function emptyReportDraft(
  parkId: number | null,
  idempotencyKey = crypto.randomUUID(),
): ReportDraft {
  return {
    kind: 'mechanic_problem',
    parkId,
    trackerKey: '',
    title: '',
    body: '',
    idempotencyKey,
    createAttempted: false,
    createdReportId: null,
  }
}

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function isPositiveId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

function parseStoredDraft(raw: string, userId: number): StoredReportDraft | null {
  try {
    const value = JSON.parse(raw) as Partial<StoredReportDraft>
    const validKind = value.kind === 'ticket_question'
      || value.kind === 'mechanic_problem'
    const validPark = value.parkId === null || isPositiveId(value.parkId)
    const validCreated = value.createdReportId === null
      || isPositiveId(value.createdReportId)
    const validIdempotencyKey = typeof value.idempotencyKey === 'string'
      && /^[A-Za-z0-9_-]{16,64}$/.test(value.idempotencyKey)
    if (
      value.version !== REPORT_DRAFT_VERSION
      || value.userId !== userId
      || typeof value.savedAt !== 'number'
      || !validKind
      || !validPark
      || typeof value.trackerKey !== 'string'
      || typeof value.title !== 'string'
      || typeof value.body !== 'string'
      || !validIdempotencyKey
      || typeof value.createAttempted !== 'boolean'
      || !validCreated
    ) return null
    return value as StoredReportDraft
  } catch {
    return null
  }
}

export function loadReportDraft(
  userId: number,
  now = Date.now(),
  storage?: Storage,
): ReportDraft | null {
  const target = resolveStorage(storage)
  if (!target) return null
  const key = reportDraftStorageKey(userId)
  let raw: string | null
  try {
    raw = target.getItem(key)
  } catch {
    return null
  }
  if (!raw) return null
  const stored = parseStoredDraft(raw, userId)
  const expired = stored == null
    || stored.savedAt > now + 5 * 60 * 1000
    || now - stored.savedAt > REPORT_DRAFT_MAX_AGE_MS
  if (expired) {
    try {
      target.removeItem(key)
    } catch {
      // Storage may be readable but not writable in a restricted browser mode.
    }
    return null
  }
  if (!stored) return null
  return {
    kind: stored.kind,
    parkId: stored.parkId,
    trackerKey: stored.trackerKey,
    title: stored.title,
    body: stored.body,
    idempotencyKey: stored.idempotencyKey,
    createAttempted: stored.createAttempted,
    createdReportId: stored.createdReportId,
  }
}

export function saveReportDraft(
  userId: number,
  draft: ReportDraft,
  now = Date.now(),
  storage?: Storage,
): void {
  const target = resolveStorage(storage)
  if (!target) return
  const stored: StoredReportDraft = {
    ...draft,
    version: REPORT_DRAFT_VERSION,
    userId,
    savedAt: now,
  }
  try {
    target.setItem(reportDraftStorageKey(userId), JSON.stringify(stored))
  } catch {
    // Disabled/quota-limited localStorage must not block report submission.
  }
}

export function clearReportDraft(userId: number, storage?: Storage): void {
  try {
    resolveStorage(storage)?.removeItem(reportDraftStorageKey(userId))
  } catch {
    // The successful server operation must not be reverted by localStorage policy.
  }
}
```

- [ ] **Step 6: Run the draft tests GREEN**

```sh
cd apps/web
npm test -- src/domains/reports/reportDraft.test.ts
git diff --check
```

Expected: all persistence cases pass; a same-session reload/offline path retains the valid draft, an auth boundary prevents restoration after numeric-ID reuse, and stored JSON contains no binary data.

- [ ] **Step 7: Write failing composer and route tests**

Create `apps/web/src/domains/reports/ReportComposerPage.test.tsx` using the existing `AuthContext.Provider`, foundation `ParkScopeProvider`, and `MemoryRouter`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type Report, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { emptyReportDraft, saveReportDraft } from './reportDraft'
import { ReportComposerPage } from './ReportComposerPage'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api } }
})

const park = { id: 7, name: 'Север', tag: 'NORTH' }
const user: User = {
  id: 3,
  username: 'mechanic',
  role: 'mechanic',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create'],
  parks: [park],
}
const created: Report = {
  id: 41,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 3,
  target_role: 'operator',
  tracker_key: null,
  tracker_url: null,
  title: 'Не включается лидар',
  body: '',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-02T09:00:00Z',
  updated_at: '2026-09-02T09:00:00Z',
  resolved_at: null,
  attachments: [],
}

function renderComposer() {
  return render(
    <AuthContext.Provider value={{
      user,
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
    }}>
      <MemoryRouter initialEntries={['/reports/new?park=7']}>
        <ParkScopeProvider>
          <Routes>
            <Route path="/reports/new" element={<ReportComposerPage />} />
            <Route path="/reports/:reportId" element={<p>Карточка репорта</p>} />
          </Routes>
        </ParkScopeProvider>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'parks').mockResolvedValue([park])
})

describe('ReportComposerPage', () => {
  it('restores text but never claims that a photo survived reload', async () => {
    saveReportDraft(3, {
      ...emptyReportDraft(7),
      title: 'Черновик лидара',
      body: 'Проверить разъём.',
    })
    renderComposer()
    expect(await screen.findByDisplayValue('Черновик лидара')).toBeInTheDocument()
    expect(screen.getByText(/сам файл не сохраняется/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Снять фото камерой')).toHaveAttribute(
      'capture',
      'environment',
    )
    expect(screen.getByLabelText('Выбрать фото из файлов')).not.toHaveAttribute('capture')
  })

  it('reuses one persisted key after a commit with a lost response', async () => {
    vi.spyOn(api, 'createReport')
      .mockRejectedValueOnce(new TypeError('response lost after commit'))
      .mockResolvedValueOnce(created)
    renderComposer()
    fireEvent.change(await screen.findByLabelText('Название'), {
      target: { value: 'Не включается лидар' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Создать репорт' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/ключ сохранены для безопасного повтора/i)
    expect(api.createReport).toHaveBeenCalledTimes(1)
    const stored = JSON.parse(localStorage.getItem('robopark:report-draft:v1:3') ?? '{}')
    expect(stored).toMatchObject({
      title: 'Не включается лидар',
      createAttempted: true,
      createdReportId: null,
    })
    expect(api.createReport).toHaveBeenNthCalledWith(1, expect.any(Object), stored.idempotencyKey)
    expect(screen.getByLabelText('Название')).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Повторить создание безопасно' }))
    await waitFor(() => expect(api.createReport).toHaveBeenCalledTimes(2))
    expect(api.createReport).toHaveBeenNthCalledWith(2, expect.any(Object), stored.idempotencyKey)
    expect(await screen.findByText('Карточка репорта')).toBeInTheDocument()
  })

  it('does not create twice when attachment retry follows POST success', async () => {
    vi.spyOn(api, 'createReport').mockResolvedValue(created)
    vi.spyOn(api, 'addReportAttachment')
      .mockRejectedValueOnce(new Error('upload failed'))
      .mockResolvedValueOnce({
        id: 9,
        kind: 'device_photo',
        filename: 'lidar.jpg',
        content_type: 'image/jpeg',
        size_bytes: 5,
      })
    renderComposer()
    fireEvent.change(await screen.findByLabelText('Название'), {
      target: { value: 'Не включается лидар' },
    })
    const photo = new File(['photo'], 'lidar.jpg', { type: 'image/jpeg' })
    fireEvent.change(screen.getByLabelText('Выбрать фото из файлов'), {
      target: { files: [photo] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Создать репорт' }))

    expect(await screen.findByText(/Репорт #41 уже создан/i)).toBeInTheDocument()
    expect(localStorage.getItem('robopark:report-draft:v1:3')).toContain(
      '"createdReportId":41',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Повторить отправку фото' }))
    await waitFor(() => {
      expect(api.createReport).toHaveBeenCalledTimes(1)
      expect(api.addReportAttachment).toHaveBeenCalledTimes(2)
    })
    expect(await screen.findByText('Карточка репорта')).toBeInTheDocument()
  })
})
```

Extend `routeManifest.test.ts`:

```ts
it('registers the report composer as guarded non-navigation content', () => {
  const route = ROUTE_MANIFEST.find((item) => item.id === 'report-new')
  expect(route).toMatchObject({
    path: '/reports/new',
    permission: 'reports.create',
    prerequisites: ['password-changed', 'approved'],
    surface: 'shell',
  })
  expect(route?.nav).toBeUndefined()
})
```

- [ ] **Step 8: Run composer tests and confirm RED**

```sh
cd apps/web
npm test -- \
  src/domains/reports/ReportComposerPage.test.tsx \
  src/app/routing/routeManifest.test.ts
```

Expected: FAIL because the composer component and `report-new` route are absent.

- [ ] **Step 9: Implement idempotent create-then-attach without duplicate POST**

Create `ReportComposerPage.tsx`; import `./reports.css`:

```tsx
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, ApiError, type ReportCreatePayload } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { resourceStore } from '../../lib/resource'
import { refreshReportsBadge } from '../../reports-badge'
import { DevicePhotoPicker } from './DevicePhotoPicker'
import {
  canCreateReport,
  parseReportFilters,
  serializeReportFilters,
} from './reportModel'
import {
  clearReportDraft,
  emptyReportDraft,
  loadReportDraft,
  saveReportDraft,
  type ReportDraft,
} from './reportDraft'
import './reports.css'

export function ReportComposerPage() {
  const { user } = useAuth()
  const parkScope = useParkScope()
  const navigate = useNavigate()
  const location = useLocation()
  const [draft, setDraft] = useState<ReportDraft>(() => user
    ? loadReportDraft(user.id) ?? emptyReportDraft(parkScope.parkId)
    : emptyReportDraft(null))
  const [photo, setPhoto] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [idempotencyConflict, setIdempotencyConflict] = useState(false)

  useEffect(() => {
    if (!user) return
    const timer = window.setTimeout(() => saveReportDraft(user.id, draft), 300)
    return () => window.clearTimeout(timer)
  }, [draft, user])

  useEffect(() => {
    if (!user || draft.createdReportId != null) return
    const allowed = user.parks.some((park) => park.id === draft.parkId)
    const fallback = user.parks.some((park) => park.id === parkScope.parkId)
      ? parkScope.parkId
      : user.parks[0]?.id ?? null
    if (!allowed && fallback !== draft.parkId) {
      setDraft((current) => ({ ...current, parkId: fallback }))
    }
  }, [draft.createdReportId, draft.parkId, parkScope.parkId, user])

  if (!user) return <LoadingState label="Загрузка формы репорта" variant="page" />
  if (!canCreateReport(user)) {
    return (
      <PageLayout title="Новый репорт">
        <EmptyState
          description="Для создания нужны право reports.create и назначенный парк."
          icon="reports"
          title="Создание репорта недоступно"
        />
      </PageLayout>
    )
  }

  const canonicalSearch = serializeReportFilters(
    parseReportFilters(location.search, { user, parkId: parkScope.parkId }),
    location.search,
  )
  const mineSearch = serializeReportFilters(
    { ...parseReportFilters(location.search, { user, parkId: parkScope.parkId }), view: 'mine' },
    location.search,
  )

  const titleError = draft.title.trim() ? undefined : 'Укажите название.'
  const trackerError = draft.kind === 'ticket_question' && !draft.trackerKey.trim()
    ? 'Для вопроса укажите ключ тикета.'
    : undefined
  const parkError = draft.parkId == null ? 'Выберите назначенный парк.' : undefined
  const invalid = Boolean(titleError || trackerError || parkError)

  function patchDraft(patch: Partial<ReportDraft>) {
    setDraft((current) => ({ ...current, ...patch }))
  }

  function finish(reportId: number) {
    clearReportDraft(user.id)
    resourceStore.invalidate(`reports:mine:${user.id}`, { prefix: true })
    resourceStore.invalidate(`reports:inbox:${user.id}:`, { prefix: true })
    refreshReportsBadge()
    navigate(`/reports/${reportId}${canonicalSearch}`, { replace: true })
  }

  async function submit() {
    if (invalid) return
    saveReportDraft(user.id, draft)
    setBusy(true)
    setError('')
    setIdempotencyConflict(false)
    let reportId = draft.createdReportId
    try {
      if (reportId == null) {
        const payload: ReportCreatePayload = {
          kind: draft.kind,
          park_id: draft.parkId as number,
          title: draft.title.trim(),
          body: draft.body.trim(),
          tracker_key: draft.trackerKey.trim() || null,
        }
        const createCheckpoint = draft.createAttempted
          ? draft
          : { ...draft, createAttempted: true }
        saveReportDraft(user.id, createCheckpoint)
        setDraft(createCheckpoint)
        const created = await api.createReport(payload, createCheckpoint.idempotencyKey)
        reportId = created.id
        const checkpoint = { ...createCheckpoint, createdReportId: reportId }
        saveReportDraft(user.id, checkpoint)
        setDraft(checkpoint)
      }
      if (photo) await api.addReportAttachment(reportId, 'device_photo', photo)
      finish(reportId)
    } catch (submitError) {
      const mapped = classifyApiError(
        submitError,
        reportId == null
          ? 'Не удалось создать репорт.'
          : `Репорт #${reportId} создан, но фото не отправлено.`,
      )
      const ambiguousCreate = reportId == null
        && ['offline', 'timeout', 'server'].includes(mapped.kind)
      const keyConflict = reportId == null
        && submitError instanceof ApiError
        && submitError.status === 409
        && submitError.detail === 'idempotency_key_reused'
      if (keyConflict) setIdempotencyConflict(true)
      if (reportId == null && !ambiguousCreate && !keyConflict) {
        const editable = { ...draft, createAttempted: false }
        saveReportDraft(user.id, editable)
        setDraft(editable)
      }
      setError(reportId == null
        ? keyConflict
          ? 'Ключ этой попытки уже связан с другим содержимым. Проверьте «Мои репорты»; автоматическая отправка остановлена.'
          : ambiguousCreate
          ? `${mapped.description} Запрос мог завершиться на сервере; черновик и ключ сохранены для безопасного повтора.`
          : mapped.description
        : `Репорт #${reportId} уже создан. ${mapped.description}`)
    } finally {
      setBusy(false)
    }
  }

  const createOutcomeUnknown = draft.createAttempted && draft.createdReportId == null
  const locked = draft.createdReportId != null || createOutcomeUnknown
  return (
    <PageLayout
      description="Текст сохраняется локально семь дней. Выбранный файл после перезагрузки нужно выбрать снова."
      title="Новый репорт"
    >
      <Panel>
        {locked && (
          <p aria-live="polite" className="rp-composer__checkpoint">
            {draft.createdReportId != null
              ? `Репорт #${draft.createdReportId} уже создан. Осталось отправить фото или продолжить без него.`
              : 'Результат создания уточняется. Поля заблокированы, безопасный повтор использует тот же ключ.'}
          </p>
        )}
        {error && <p role="alert">{error}</p>}
        <form className="rp-composer" onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}>
          <FormField id="report-create-kind" label="Тип репорта" required>
            <select
              disabled={locked || busy}
              id="report-create-kind"
              onChange={(event) => patchDraft({
                kind: event.target.value as ReportDraft['kind'],
              })}
              value={draft.kind}
            >
              <option value="mechanic_problem">Проблема с роботом или работой</option>
              <option value="ticket_question">Вопрос по тикету</option>
            </select>
          </FormField>
          <FormField id="report-create-park" error={parkError} label="Парк" required>
            <select
              disabled={locked || busy}
              id="report-create-park"
              onChange={(event) => {
                const parkId = Number(event.target.value)
                patchDraft({ parkId })
                parkScope.setParkId(parkId, { replace: true })
              }}
              value={draft.parkId ?? ''}
            >
              {user.parks.map((park) => (
                <option key={park.id} value={park.id}>{park.name} ({park.tag})</option>
              ))}
            </select>
          </FormField>
          {draft.kind === 'ticket_question' && (
            <FormField
              id="report-create-tracker"
              error={trackerError}
              label="Ключ тикета"
              required
            >
              <input
                disabled={locked || busy}
                id="report-create-tracker"
                maxLength={128}
                onChange={(event) => patchDraft({ trackerKey: event.target.value })}
                value={draft.trackerKey}
              />
            </FormField>
          )}
          <FormField id="report-create-title" error={titleError} label="Название" required>
            <input
              disabled={locked || busy}
              id="report-create-title"
              maxLength={256}
              onChange={(event) => patchDraft({ title: event.target.value })}
              value={draft.title}
            />
          </FormField>
          <FormField id="report-create-body" label="Описание">
            <textarea
              disabled={locked || busy}
              id="report-create-body"
              onChange={(event) => patchDraft({ body: event.target.value })}
              rows={6}
              value={draft.body}
            />
          </FormField>
          <DevicePhotoPicker disabled={busy} file={photo} onFileChange={setPhoto} />
          <div className="rp-composer__actions">
            <Button
              busy={busy}
              disabled={busy || invalid || (draft.createdReportId != null && !photo)}
              leadingIcon="send"
              type="submit"
            >
              {draft.createdReportId != null
                ? 'Повторить отправку фото'
                : createOutcomeUnknown
                  ? 'Повторить создание безопасно'
                  : 'Создать репорт'}
            </Button>
            {draft.createdReportId != null && (
              <Button
                disabled={busy}
                onClick={() => finish(draft.createdReportId as number)}
                type="button"
                variant="secondary"
              >
                Продолжить без фото
              </Button>
            )}
            {idempotencyConflict && (
              <Button
                onClick={() => navigate(`/reports${mineSearch}`)}
                type="button"
                variant="secondary"
              >
                Открыть мои репорты
              </Button>
            )}
            <Button
              disabled={busy}
              onClick={() => navigate(`/reports${canonicalSearch}`)}
              type="button"
              variant="ghost"
            >
              Вернуться к репортам
            </Button>
          </div>
        </form>
      </Panel>
    </PageLayout>
  )
}
```

Build the create payload from `createCheckpoint`, not a later render, so a same-key retry is byte-for-byte equivalent after normalization. Persist `createAttempted=true` synchronously before the first request. Only `offline`, `timeout`, or `server` is an ambiguous create outcome: keep fields locked and the key unchanged until retry returns the existing/new row. A definitive client/authorization/configuration response resets `createAttempted=false` and unlocks editing because that request did not commit. Treat exact `409 idempotency_key_reused` as a special fail-closed conflict: keep the form locked, offer a link to «Мои репорты», and never rotate the key or auto-submit. Because `finish` only runs after a successful upload or explicit «Продолжить без фото», an upload failure remains recoverable. A successful create persists the positive ID before upload and suppresses all later create calls.

- [ ] **Step 10: Register `/reports/new` without a second navigation entry**

Extend `AppRouteId` with:

```ts
  | 'report-new'
```

Add to `ROUTE_MANIFEST`:

```ts
  {
    id: 'report-new',
    path: '/reports/new',
    label: 'Новый репорт',
    icon: 'reports',
    permission: 'reports.create',
    prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
    surface: 'shell',
  },
```

Import `ReportComposerPage` in `AppRouter.tsx` and extend `ROUTE_ELEMENTS`:

```tsx
  'report-new': <ReportComposerPage />,
```

Keep the static `/reports/new` route before `/reports/:reportId` when mapping the manifest to React Router, so the word `new` cannot be parsed as a report id.

Insert this row into the cumulative independent `protectedRoutes` literal in `accessPolicy.test.ts`:

```ts
{ id: 'report-new', permission: 'reports.create', operatorOnly: false, mechanicPark: true },
```

The existing Cartesian loops must exercise the row's primary `reports.create` permission across every role/status/permission/password/park axis. In `canAccessRoute`, add one centralized `report-new` condition requiring `nav.reports` as well; do not change the frozen single-string manifest permission field. Add direct `AppRouter.test.tsx` coverage that a mechanic without a park cannot mount the composer, a create-only custom role cannot mount it, and a custom role with both `nav.reports` and `reports.create` plus an assigned park can. `canCreateReport` uses the same two-permission rule, so every successful create may navigate to `/reports/:id` and every Back action may reach `/reports`.

- [ ] **Step 11: Add mobile-first composer styles**

Append to `reports.css`:

```css
.rp-composer {
  display: grid;
  gap: var(--rp-space-4);
  max-width: 48rem;
}

.rp-composer input,
.rp-composer select,
.rp-composer textarea {
  width: 100%;
  min-width: 0;
}

.rp-composer__checkpoint {
  padding: var(--rp-space-3);
  border: 1px solid var(--rp-info);
  border-radius: var(--rp-radius-control);
  background: var(--rp-info-surface);
}

.rp-composer__actions {
  display: flex;
  gap: var(--rp-space-2);
  flex-wrap: wrap;
}

@media (max-width: 599px) {
  .rp-composer input,
  .rp-composer select,
  .rp-composer textarea {
    font-size: 1rem;
  }

  .rp-composer__actions {
    position: sticky;
    z-index: 1;
    bottom: var(--rp-space-2);
    display: grid;
    padding: var(--rp-space-2);
    border: 1px solid var(--rp-border);
    border-radius: var(--rp-radius-panel);
    background: var(--rp-surface-elevated);
    box-shadow: var(--rp-shadow-panel);
  }
}
```

The shell already reserves its mobile navigation inset; the sticky action uses only guaranteed foundation tokens.

- [ ] **Step 12: Run GREEN and composer regressions**

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_reports.py tests/test_models_migration.py
uv run --frozen alembic heads
uv run --frozen --extra dev ruff check \
  src/robopark_api/models.py src/robopark_api/services/reports.py \
  src/robopark_api/routers/reports.py tests/test_reports.py tests/test_models_migration.py
cd ../web
npm test -- \
  src/api.report-create.test.ts \
  src/domains/reports/reportDraft.test.ts \
  src/domains/reports/ReportComposerPage.test.tsx \
  src/domains/reports/ReportWorkspacePage.test.tsx \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts \
  src/app/routing/AppRouter.test.tsx
npm run check-nav
npm run lint
npm run build
git diff --check
```

Expected: migration/idempotency, same-session draft restore, reused-ID session-boundary purge, ambiguous-response replay, upload retry and non-nav composer route all pass; `alembic heads` prints exactly `0016_report_idempotency (head)` at the Phase 3 boundary; one lost create response followed by retry leaves one server row under the same key, while one failed photo upload records one create call and two attachment calls.

- [ ] **Step 13: Commit**

```sh
git add \
  apps/api/src/robopark_api/models.py \
  apps/api/src/robopark_api/services/reports.py \
  apps/api/src/robopark_api/routers/reports.py \
  apps/api/alembic/versions/0016_report_idempotency.py \
  apps/api/tests/test_reports.py \
  apps/api/tests/test_models_migration.py \
  apps/web/src/api.ts \
  apps/web/src/api.report-create.test.ts \
  apps/web/src/domains/reports/reportDraft.ts \
  apps/web/src/domains/reports/reportDraft.test.ts \
  apps/web/src/domains/reports/ReportComposerPage.tsx \
  apps/web/src/domains/reports/ReportComposerPage.test.tsx \
  apps/web/src/domains/reports/reports.css \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/accessPolicy.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/app/routing/AppRouter.test.tsx \
  apps/web/src/app/routing/routeManifest.test.ts \
  apps/web/src/app/routing/accessPolicy.test.ts
git commit -m "feat(reports): add idempotent recoverable composer"
```

---

### Task 6: Lock report journeys, responsive behavior, themes, and accessibility

**Files:**
- Create: `apps/web/e2e/reports/fixtures.ts`
- Create: `apps/web/e2e/reports/reports.spec.ts`
- Create: `apps/web/e2e/reports/reports.visual.spec.ts`
- Create: `apps/web/e2e/reports/reports.visual.spec.ts-snapshots/*.png`

**Interfaces:**
- Consumes unchanged: `installMockApi(page, { user, parks, routes })`, `MockRoute`, `assertNoSeriousA11yViolations(page)`, theme key `robopark-theme`.
- Produces: `createReportMock()` returning fresh mutable fixture state per test, its `routes: readonly MockRoute[]`, and call counters for create/attachment/lifecycle requests.
- Isolation: every HTTP response comes from `MockApiOptions.routes` or foundation auth/park defaults. No test may use an unmocked API response, seeded local database, remote origin, cookie, or real attachment.

- [ ] **Step 1: Create deterministic stateful report routes**

Create `apps/web/e2e/reports/fixtures.ts`:

```ts
import type { Report, ReportAttachment } from '../../src/api'
import type { MockRoute } from '../support/mockApi'

const initialReport: Report = {
  id: 41,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 3,
  target_role: 'operator',
  tracker_key: null,
  tracker_url: null,
  title: 'Не включается лидар',
  body: 'После перезапуска ошибка остаётся.',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-02T07:00:00Z',
  updated_at: '2026-09-02T07:00:00Z',
  resolved_at: null,
  attachments: [{
    id: 8,
    kind: 'client_log',
    filename: 'client.log',
    content_type: 'text/plain',
    size_bytes: 18,
  }],
}

function idFrom(request: Request): number {
  const match = new URL(request.url).pathname.match(/\/reports\/(\d+)/)
  if (!match) throw new Error(`Missing report id in ${request.url}`)
  return Number(match[1])
}

export function createReportMock(options: { failFirstCreateAfterPersist?: boolean } = {}) {
  let nextReportId = 42
  let nextAttachmentId = 9
  const reports: Report[] = [{ ...initialReport, attachments: [...initialReport.attachments] }]
  const createdByKey = new Map<string, Report>()
  const calls = { createRequests: 0, createdRows: 0, attachment: 0, done: 0, returned: 0, escalate: 0 }
  const find = (id: number) => reports.find((report) => report.id === id)

  const routes: MockRoute[] = [
    {
      method: 'GET',
      path: '/api/reports/inbox',
      handler: (request) => {
        const park = Number(new URL(request.url).searchParams.get('park_id'))
        return {
          json: reports.filter((report) => report.status === 'open'
            && report.target_role === 'operator'
            && (!park || report.park_id === park)),
        }
      },
    },
    {
      method: 'GET',
      path: '/api/reports/mine',
      handler: () => ({ json: reports.filter((report) => report.author_user_id === 3) }),
    },
    {
      method: 'GET',
      path: /^\/api\/reports\/\d+$/,
      handler: (request) => {
        const report = find(idFrom(request))
        return report ? { json: report } : { status: 404, json: { detail: 'report_not_found' } }
      },
    },
    {
      method: 'POST',
      path: '/api/reports',
      handler: async (request) => {
        calls.createRequests += 1
        const key = request.headers.get('Idempotency-Key')
        if (!key) return { status: 422, json: { detail: 'idempotency_key_required_by_fixture' } }
        const input = await request.json() as {
          kind: string
          park_id: number
          title: string
          body?: string
          tracker_key?: string | null
        }
        const existing = createdByKey.get(key)
        if (existing) return { status: 201, json: existing }
        const report: Report = {
          ...initialReport,
          id: nextReportId++,
          kind: input.kind,
          park_id: input.park_id,
          author_user_id: 3,
          title: input.title,
          body: input.body ?? '',
          tracker_key: input.tracker_key ?? null,
          attachments: [],
        }
        reports.unshift(report)
        createdByKey.set(key, report)
        calls.createdRows += 1
        if (options.failFirstCreateAfterPersist && calls.createdRows === 1) {
          return { status: 503, json: { detail: 'synthetic_response_lost' } }
        }
        return { status: 201, json: report }
      },
    },
    {
      method: 'POST',
      path: /^\/api\/reports\/\d+\/attachments$/,
      handler: async (request) => {
        calls.attachment += 1
        const report = find(idFrom(request))
        if (!report) return { status: 404, json: { detail: 'report_not_found' } }
        const form = await request.formData()
        const file = form.get('file')
        const attachment: ReportAttachment = {
          id: nextAttachmentId++,
          kind: 'device_photo',
          filename: file instanceof File ? file.name : 'robot.jpg',
          content_type: file instanceof File ? file.type : 'image/jpeg',
          size_bytes: file instanceof File ? file.size : 0,
        }
        report.attachments = [...report.attachments, attachment]
        return { status: 201, json: attachment }
      },
    },
    {
      method: 'GET',
      path: /^\/api\/reports\/\d+\/attachments\/\d+$/,
      handler: () => ({
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="96"><rect width="160" height="96" fill="#777"/></svg>',
        headers: { 'Content-Type': 'image/svg+xml' },
      }),
    },
    {
      method: 'POST',
      path: /^\/api\/reports\/\d+\/return$/,
      handler: async (request) => {
        calls.returned += 1
        const report = find(idFrom(request))
        if (!report) return { status: 404, json: { detail: 'report_not_found' } }
        const input = await request.json() as { comment: string }
        report.status = 'returned'
        report.return_comment = input.comment
        report.updated_at = '2026-09-02T08:00:00Z'
        return { json: report }
      },
    },
    {
      method: 'POST',
      path: /^\/api\/reports\/\d+\/done$/,
      handler: (request) => {
        calls.done += 1
        const report = find(idFrom(request))
        if (!report) return { status: 404, json: { detail: 'report_not_found' } }
        report.status = 'done'
        report.resolved_at = '2026-09-02T08:00:00Z'
        report.updated_at = report.resolved_at
        return { json: report }
      },
    },
    {
      method: 'POST',
      path: /^\/api\/reports\/\d+\/escalate$/,
      handler: async (request) => {
        calls.escalate += 1
        const parent = find(idFrom(request))
        if (!parent) return { status: 404, json: { detail: 'report_not_found' } }
        const input = await request.json() as { comment: string }
        const child: Report = {
          ...parent,
          id: nextReportId++,
          kind: 'escalation_to_admin',
          author_user_id: 2,
          target_role: 'admin',
          title: `Эскалация: ${parent.title}`,
          body: input.comment,
          parent_report_id: parent.id,
          attachments: [],
        }
        reports.unshift(child)
        return { json: child }
      },
    },
  ]

  return { calls, reports, routes }
}
```

The neutral inline SVG is mock transport content only, not a robot photo or production asset. The fixture exposes no route for unsupported SLA, assignment, or audit-log endpoints.

- [ ] **Step 2: Add desktop, mobile, recovery, deep-link, and a11y journeys**

Create `apps/web/e2e/reports/reports.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import type { User } from '../../src/api'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installMockApi } from '../support/mockApi'
import { createReportMock } from './fixtures'

const park = { id: 7, name: 'Север', tag: 'NORTH' }
const operator: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.resolve'],
  parks: [park],
}
const mechanic: User = {
  id: 3,
  username: 'mechanic',
  role: 'mechanic',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.create'],
  parks: [park],
}

test('desktop keeps inbox and canonical detail in one workspace', async ({ page }) => {
  const mock = createReportMock()
  await installMockApi(page, { user: operator, parks: [park], routes: mock.routes })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/reports?park=7')

  await expect(page.getByRole('heading', { name: 'Репорты' })).toBeVisible()
  await page.getByRole('link', { name: /Не включается лидар/ }).click()
  await expect(page).toHaveURL(/\/reports\/41\?.*park=7/)
  await expect(page.getByRole('heading', { name: 'Не включается лидар' })).toBeVisible()
  await expect(page.getByText(/не является полным журналом действий/i)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Скачать client.log' })).toHaveAttribute(
    'href',
    '/api/reports/41/attachments/8',
  )

  await page.getByRole('button', { name: 'Вернуть автору' }).click()
  await page.getByLabel('Комментарий для автора').fill('Добавьте фото разъёма.')
  await page.getByRole('button', { name: 'Подтвердить возврат' }).click()
  await expect(page.getByText('Возвращён')).toBeVisible()
  expect(mock.calls.returned).toBe(1)
})

test('mobile restores text draft, requires file reselection, then creates with photo', async ({ page }) => {
  const mock = createReportMock()
  await installMockApi(page, { user: mechanic, parks: [park], routes: mock.routes })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/reports/new?park=7')
  await page.getByLabel('Название').fill('Проверить лидар')
  await page.getByLabel('Описание').fill('Ошибка после перезапуска')
  await page.waitForTimeout(350)
  await page.reload()

  await expect(page.getByLabel('Название')).toHaveValue('Проверить лидар')
  await expect(page.getByText(/сам файл не сохраняется/i)).toBeVisible()
  await expect(page.getByLabel('Выбрать фото из файлов')).toHaveValue('')
  await page.getByLabel('Выбрать фото из файлов').setInputFiles({
    name: 'lidar.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('neutral-test-image'),
  })
  await page.getByRole('button', { name: 'Создать репорт' }).click()

  await expect(page).toHaveURL(/\/reports\/42\?.*park=7/)
  await expect(page.getByRole('heading', { name: 'Проверить лидар' })).toBeVisible()
  expect(mock.calls.createRequests).toBe(1)
  expect(mock.calls.createdRows).toBe(1)
  expect(mock.calls.attachment).toBe(1)
})

test('ambiguous create retry reuses its key and produces one report', async ({ page }) => {
  const mock = createReportMock({ failFirstCreateAfterPersist: true })
  await installMockApi(page, { user: mechanic, parks: [park], routes: mock.routes })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/reports/new?park=7')
  await page.getByLabel('Название').fill('Проверить лидар')
  await page.getByRole('button', { name: 'Создать репорт' }).click()

  await expect(page.getByRole('alert')).toContainText('ключ сохранены для безопасного повтора')
  await expect(page.getByLabel('Название')).toBeDisabled()
  await page.getByRole('button', { name: 'Повторить создание безопасно' }).click()

  await expect(page).toHaveURL(/\/reports\/42\?.*park=7/)
  expect(mock.calls.createRequests).toBe(2)
  expect(mock.calls.createdRows).toBe(1)
})

test('direct detail link remains usable and 320 px has no page overflow', async ({ page }) => {
  const mock = createReportMock()
  await installMockApi(page, { user: operator, parks: [park], routes: mock.routes })
  await page.setViewportSize({ width: 320, height: 720 })
  await page.goto('/reports/41?view=inbox&status=all&age=all&kind=all&park=7')

  await expect(page.getByRole('heading', { name: 'Не включается лидар' })).toBeVisible()
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
  await assertNoSeriousA11yViolations(page)
  await expect(page.getByText(/SLA/i)).toHaveCount(0)
  await expect(page.getByText(/Ответственный/i)).toHaveCount(0)
})
```

Add one session-boundary browser journey in the same file. Seed a saved draft and recent-robots-v2 entry for an old account `id=3`, execute logout, then make the next login return a newly created synthetic account with the same numeric `id=3`. Navigate to `/reports/new` and assert title/body/idempotency checkpoint are empty/new and no stale recent robot is visible. Capture storage at the login request boundary and assert both protected prefixes were removed **before** authentication. Keep the mobile reload case above as the control proving an ordinary same-session reload still restores the draft; add an offline/reconnect reload variant that does the same without logging out.

- [ ] **Step 3: Run the journey suite and fix any integration RED in its owning task**

```sh
cd apps/web
npm run test:e2e -- e2e/reports/reports.spec.ts --project=chromium
```

Expected: PASS. If a journey fails, first confirm all requests are caught by the fixture, then fix the corresponding Task 1–5 source file and rerun its focused unit test before rerunning this suite. Never point Playwright at a runtime API.

- [ ] **Step 4: Add the ten deterministic theme/viewport snapshots**

Create `apps/web/e2e/reports/reports.visual.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import type { User } from '../../src/api'
import { installMockApi } from '../support/mockApi'
import { createReportMock } from './fixtures'

const park = { id: 7, name: 'Север', tag: 'NORTH' }
const user: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.reports', 'reports.resolve'],
  parks: [park],
}
const heights: Record<number, number> = {
  320: 720,
  390: 844,
  768: 1024,
  1024: 768,
  1440: 1000,
}

for (const theme of ['light', 'dark'] as const) {
  for (const width of [320, 390, 768, 1024, 1440] as const) {
    test(`reports detail ${theme} at ${width}px`, async ({ page }) => {
      const mock = createReportMock()
      await page.addInitScript((preference) => {
        localStorage.setItem('robopark-theme', preference)
      }, theme)
      await installMockApi(page, { user, parks: [park], routes: mock.routes })
      await page.setViewportSize({ width, height: heights[width] })
      await page.goto('/reports/41?view=inbox&status=all&age=all&kind=all&park=7')
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.getByRole('heading', { name: 'Не включается лидар' })).toBeVisible()
      await expect(page).toHaveScreenshot(`reports-${theme}-${width}.png`, {
        animations: 'disabled',
        fullPage: true,
      })
    })
  }
}
```

- [ ] **Step 5: Confirm the missing-baseline RED, then write approved baselines**

```sh
cd apps/web
npm run test:e2e:linux -- e2e/reports/reports.visual.spec.ts --project=chromium
```

Expected first run: FAIL with ten missing snapshots.

Inspect every received screenshot for correct compact/sequential behavior at 320/390/768, split view at 1024/1440, visible text status, legible attachment metadata, and no clipping. Then run:

```sh
cd apps/web
npm run test:e2e:update:linux -- e2e/reports/reports.visual.spec.ts --project=chromium
npm run test:e2e:linux -- e2e/reports/reports.visual.spec.ts --project=chromium
git diff --check
```

Expected: ten light/dark baselines are written under the Playwright snapshot directory and the immediate rerun passes.

- [ ] **Step 6: Commit**

```sh
git add \
  apps/web/e2e/reports/fixtures.ts \
  apps/web/e2e/reports/reports.spec.ts \
  apps/web/e2e/reports/reports.visual.spec.ts \
  apps/web/e2e/reports/reports.visual.spec.ts-snapshots
git commit -m "test(web): cover report workspace journeys"
```

---

### Task 7: Define analytics availability and non-invented projections

**Files:**
- Modify: `apps/web/src/app/routing/accessPolicy.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Create: `apps/web/src/domains/analytics/analyticsModel.ts`
- Create: `apps/web/src/domains/analytics/analyticsModel.test.ts`

**Interfaces:**
- Consumes: `AccessUser`, Foundation `hasFleetParkScope`/`PARK_QUERY_KEY`, `DashboardHistory`, `DashboardHistoryPoint`, `NowReport`.
- Produces: `hasAnalyticsScope(user)` in `accessPolicy.ts`; `AnalyticsRange = 7 | 14 | 30`, `AnalyticsSectionId = 'blocker-flow' | 'park-comparison'`, `AnalyticsQueryState`, `DailyBlockerFlow`, `BlockerFlowModel`, `ParkComparisonRow`, `ParkComparisonModel`, `DEFERRED_ANALYTICS_FEATURES`, `analyticsSectionsFor`, `parseAnalyticsQuery`, `serializeAnalyticsQuery`, `buildBlockerFlow`, `buildParkComparison`, `analyticsFreshness`.
- Truth invariant: aggregation uses only valid response points; exact duplicate `bucket_start` values count once; absent buckets/days are omitted instead of zero-filled; missing per-park metric keys become `null`, rendered as «Нет данных».
- Scope invariant: blocker flow uses the explicit foundation `park`; it is available to any approved role with `nav.analytics` plus an assigned park, or to admin/royal/a `parks.manage` actor with fleet scope. Comparison uses all assigned operator parks and must say that the selected global park does not constrain it. A route is never visible when `analyticsSectionsFor` would be empty.

- [ ] **Step 1: Write failing analytics model tests**

Create `apps/web/src/domains/analytics/analyticsModel.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { AccessUser } from '../../app/routing/accessPolicy'
import type { DashboardHistory, NowReport } from '../../api'
import {
  DEFERRED_ANALYTICS_FEATURES,
  analyticsFreshness,
  analyticsSectionsFor,
  buildBlockerFlow,
  buildParkComparison,
  parseAnalyticsQuery,
  serializeAnalyticsQuery,
} from './analyticsModel'

const parks = [
  { id: 7, name: 'Север', tag: 'NORTH' },
  { id: 8, name: 'Юг', tag: 'SOUTH' },
]
const operator: AccessUser = {
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.analytics'],
  parks,
}

describe('analytics availability and query', () => {
  it('publishes only sections backed by current endpoints', () => {
    expect(analyticsSectionsFor(operator)).toEqual([
      'blocker-flow',
      'park-comparison',
    ])
    expect(analyticsSectionsFor({ ...operator, parks: [parks[0]] })).toEqual([
      'blocker-flow',
    ])
    expect(analyticsSectionsFor({ ...operator, role: 'driver', parks: [parks[0]] })).toEqual([
      'blocker-flow',
    ])
    expect(analyticsSectionsFor({ ...operator, role: 'field_lead', parks: [parks[0]] })).toEqual([
      'blocker-flow',
    ])
    expect(analyticsSectionsFor({ ...operator, role: 'driver', parks: [] })).toEqual([])
    expect(analyticsSectionsFor({
      ...operator,
      role: 'field_lead',
      parks: [],
      permissions: ['nav.analytics', 'parks.manage'],
    })).toEqual(['blocker-flow'])
    expect(analyticsSectionsFor({ ...operator, permissions: [], parks: [parks[0]] })).toEqual([])
    expect(DEFERRED_ANALYTICS_FEATURES).toEqual(['sla', 'recurring-failures'])
  })

  it('keeps only range and the foundation park query', () => {
    const state = parseAnalyticsQuery('?days=14&park=7&metric=sla')
    expect(state).toEqual({ days: 14 })
    expect(serializeAnalyticsQuery(state, '?park=7&metric=sla')).toBe(
      '?days=14&park=7',
    )
  })
})

describe('blocker flow projection', () => {
  it('deduplicates exact buckets, groups in Moscow time, and never fills gaps', () => {
    const response: DashboardHistory = {
      park_id: 7,
      points: [
        {
          bucket_start: '2026-09-01T20:00:00Z',
          arrived_count: 3,
          departed_count: 1,
        },
        {
          bucket_start: '2026-09-01T20:00:00Z',
          arrived_count: 3,
          departed_count: 1,
        },
        {
          bucket_start: '2026-09-02T02:00:00Z',
          arrived_count: 2,
          departed_count: 4,
        },
      ],
    }

    const model = buildBlockerFlow(response, 7)

    expect(model.days).toEqual([
      {
        dateKey: '2026-09-01',
        label: '1 сент.',
        arrived: 3,
        departed: 1,
        bucketCount: 1,
      },
      {
        dateKey: '2026-09-02',
        label: '2 сент.',
        arrived: 2,
        departed: 4,
        bucketCount: 1,
      },
    ])
    expect(model).toMatchObject({
      totalArrived: 5,
      totalDeparted: 5,
      netFlow: 0,
      pointCount: 2,
      maximumPointCount: 84,
      isPartial: true,
      latestBucketAt: '2026-09-02T02:00:00Z',
      unit: 'блокеров',
    })
    expect(model.days).toHaveLength(2)
  })

  it('uses the bucket timestamp itself for freshness', () => {
    expect(analyticsFreshness(
      '2026-09-02T06:00:00Z',
      new Date('2026-09-02T09:00:00Z'),
    )).toBe('fresh')
    expect(analyticsFreshness(
      '2026-09-02T00:00:00Z',
      new Date('2026-09-02T09:00:00Z'),
    )).toBe('stale')
    expect(analyticsFreshness(null)).toBe('offline')
  })
})

describe('park comparison projection', () => {
  it('sorts real blocker counts and keeps absent metrics unknown', () => {
    const response: NowReport = {
      generated_at: '2026-09-02T12:00:00+03:00',
      scope: 'all',
      totals: { blocker: 999 },
      parks: [
        {
          park_id: 7,
          park_name: 'Север',
          park_tag: 'NORTH',
          metrics: { open_blockers: 2, queued: 1, waiting_parts: 1 },
        },
        {
          park_id: 8,
          park_name: 'Юг',
          park_tag: 'SOUTH',
          metrics: { open_blockers: 5, backlog: 3, waiting_team: 2 },
        },
      ],
      skipped_parks: [{
        park_id: 9,
        park_name: 'Запад',
        reason: 'no_tracker_queue',
      }],
    }

    const model = buildParkComparison(response)

    expect(model.rows.map((row) => row.parkId)).toEqual([8, 7])
    expect(model.rows[0]).toMatchObject({
      openBlockers: 5,
      backlog: 3,
      queued: null,
      waitingTeam: 2,
      waitingParts: null,
    })
    expect(model.skipped).toEqual([{
      parkId: 9,
      parkName: 'Запад',
      reason: 'Не настроена очередь Tracker',
    }])
    expect(JSON.stringify(model)).not.toContain('999')
  })
})
```

In `accessPolicy.test.ts`, add a pure predicate test before any route wiring exists: assigned-park driver and `field_lead` return true; either role with no parks returns false; custom `parks.manage`, admin, and royal return true without assigned parks. This makes Task 7 independently GREEN and prevents `analyticsModel.ts` from importing an export created only by a later task.

- [ ] **Step 2: Run the pure tests and confirm RED**

```sh
cd apps/web
npm test -- src/domains/analytics/analyticsModel.test.ts
```

Expected: FAIL because `analyticsModel.ts` does not exist.

- [ ] **Step 3: Implement URL state and endpoint-backed section gates**

First produce the shared predicate in `accessPolicy.ts`:

```ts
import { hasFleetParkScope } from '../park/parkScope'

export function hasAnalyticsScope(user: AccessUser): boolean {
  return hasFleetParkScope(user) || user.parks.length > 0
}
```

Do not change `canAccessRoute` yet; Task 8 wires this predicate to the Analytics route atomically with the page/API change.

Create `apps/web/src/domains/analytics/analyticsModel.ts`:

```ts
import { PARK_QUERY_KEY } from '../../app/park/parkScope'
import { hasAnalyticsScope, type AccessUser } from '../../app/routing/accessPolicy'
import type {
  DashboardHistory,
  DashboardHistoryPoint,
  NowReport,
} from '../../api'
import type { Freshness } from '../../design-system/feedback/AsyncState'

export type AnalyticsRange = 7 | 14 | 30
export type AnalyticsSectionId = 'blocker-flow' | 'park-comparison'
export type AnalyticsQueryState = { days: AnalyticsRange }

export const DEFERRED_ANALYTICS_FEATURES = [
  'sla',
  'recurring-failures',
] as const

export function analyticsSectionsFor(user: AccessUser): AnalyticsSectionId[] {
  if (!(user.permissions ?? []).includes('nav.analytics')) return []
  if (!hasAnalyticsScope(user)) return []
  return user.role === 'operator' && user.parks.length > 1
    ? ['blocker-flow', 'park-comparison']
    : ['blocker-flow']
}

export function parseAnalyticsQuery(search: string): AnalyticsQueryState {
  const value = Number(new URLSearchParams(search).get('days'))
  return { days: value === 14 || value === 30 ? value : 7 }
}

export function serializeAnalyticsQuery(
  state: AnalyticsQueryState,
  currentSearch: string,
): string {
  const current = new URLSearchParams(currentSearch)
  const query = new URLSearchParams()
  query.set('days', String(state.days))
  const park = current.get(PARK_QUERY_KEY)
  if (park) query.set(PARK_QUERY_KEY, park)
  return `?${query.toString()}`
}

const validCount = (value: number): boolean => Number.isInteger(value) && value >= 0

function validPoint(point: DashboardHistoryPoint): boolean {
  return Number.isFinite(new Date(point.bucket_start).getTime())
    && validCount(point.arrived_count)
    && validCount(point.departed_count)
}

function moscowDate(value: string): { dateKey: string; label: string } {
  const date = new Date(value)
  const keyParts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Moscow',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    keyParts.find((part) => part.type === type)?.value ?? ''
  return {
    dateKey: `${read('year')}-${read('month')}-${read('day')}`,
    label: new Intl.DateTimeFormat('ru-RU', {
      timeZone: 'Europe/Moscow',
      day: 'numeric',
      month: 'short',
    }).format(date),
  }
}
```

- [ ] **Step 4: Aggregate only observed history buckets**

Append to `analyticsModel.ts`:

```ts
export type DailyBlockerFlow = {
  dateKey: string
  label: string
  arrived: number
  departed: number
  bucketCount: number
}

export type BlockerFlowModel = {
  parkId: number
  rangeDays: AnalyticsRange
  days: DailyBlockerFlow[]
  totalArrived: number
  totalDeparted: number
  netFlow: number
  pointCount: number
  maximumPointCount: number
  isPartial: boolean
  latestBucketAt: string | null
  unit: 'блокеров'
}

export function buildBlockerFlow(
  response: DashboardHistory,
  rangeDays: AnalyticsRange,
): BlockerFlowModel {
  const unique = new Map<number, DashboardHistoryPoint>()
  for (const point of response.points) {
    if (validPoint(point)) unique.set(new Date(point.bucket_start).getTime(), point)
  }
  const points = [...unique.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, point]) => point)
  const byDay = new Map<string, DailyBlockerFlow>()
  for (const point of points) {
    const day = moscowDate(point.bucket_start)
    const current = byDay.get(day.dateKey) ?? {
      ...day,
      arrived: 0,
      departed: 0,
      bucketCount: 0,
    }
    current.arrived += point.arrived_count
    current.departed += point.departed_count
    current.bucketCount += 1
    byDay.set(day.dateKey, current)
  }
  const days = [...byDay.values()]
  const totalArrived = days.reduce((sum, day) => sum + day.arrived, 0)
  const totalDeparted = days.reduce((sum, day) => sum + day.departed, 0)
  const maximumPointCount = rangeDays * 12
  return {
    parkId: response.park_id,
    rangeDays,
    days,
    totalArrived,
    totalDeparted,
    netFlow: totalArrived - totalDeparted,
    pointCount: points.length,
    maximumPointCount,
    isPartial: points.length < maximumPointCount,
    latestBucketAt: points.at(-1)?.bucket_start ?? null,
    unit: 'блокеров',
  }
}

export function analyticsFreshness(
  latestAt: string | null,
  now = new Date(),
): Freshness {
  if (!latestAt) return 'offline'
  const timestamp = new Date(latestAt).getTime()
  if (!Number.isFinite(timestamp)) return 'offline'
  return now.getTime() - timestamp <= 4 * 60 * 60 * 1000 ? 'fresh' : 'stale'
}
```

`maximumPointCount` is a stated upper bound from the two-hour scanner cadence, not a promise that every boundary must have a row. The UI copy in Task 8 must say «из максимум», never «пропущено N».

- [ ] **Step 5: Project comparison rows without using duplicated totals**

Append to `analyticsModel.ts`:

```ts
export type ParkComparisonRow = {
  parkId: number
  parkName: string
  parkTag: string
  openBlockers: number | null
  backlog: number | null
  queued: number | null
  waitingTeam: number | null
  waitingParts: number | null
}

export type ParkComparisonModel = {
  generatedAt: string
  rows: ParkComparisonRow[]
  skipped: Array<{ parkId: number; parkName: string; reason: string }>
}

function optionalMetric(metrics: Record<string, number>, key: string): number | null {
  const value = metrics[key]
  return validCount(value) ? value : null
}

const skipReason: Record<string, string> = {
  reports_disabled: 'Сбор отчётов выключен',
  no_tracker_queue: 'Не настроена очередь Tracker',
}

export function buildParkComparison(response: NowReport): ParkComparisonModel {
  const rows = response.parks.map((park): ParkComparisonRow => ({
    parkId: park.park_id,
    parkName: park.park_name,
    parkTag: park.park_tag,
    openBlockers: optionalMetric(park.metrics, 'open_blockers'),
    backlog: optionalMetric(park.metrics, 'backlog'),
    queued: optionalMetric(park.metrics, 'queued'),
    waitingTeam: optionalMetric(park.metrics, 'waiting_team'),
    waitingParts: optionalMetric(park.metrics, 'waiting_parts'),
  })).sort((left, right) => {
    if (left.openBlockers == null) return right.openBlockers == null ? 0 : 1
    if (right.openBlockers == null) return -1
    return right.openBlockers - left.openBlockers
      || left.parkName.localeCompare(right.parkName, 'ru')
  })

  return {
    generatedAt: response.generated_at,
    rows,
    skipped: response.skipped_parks.map((park) => ({
      parkId: park.park_id,
      parkName: park.park_name,
      reason: skipReason[park.reason] ?? park.reason,
    })),
  }
}
```

Do not read or expose `response.totals`, `arrived`, or `done`: those current-day figures already belong to Overview. Comparison is limited to cross-park blocker composition.

- [ ] **Step 6: Run GREEN and static honesty checks**

```sh
cd apps/web
npm test -- src/domains/analytics/analyticsModel.test.ts src/app/routing/accessPolicy.test.ts
npm run lint
npm run build
rg -n "due_at|sla_state|assignee|recurring" src/domains/analytics
git diff --check
```

Expected: model tests pass; the final `rg` finds only `DEFERRED_ANALYTICS_FEATURES` and its test, never a rendered model field.

- [ ] **Step 7: Commit**

```sh
git add \
  apps/web/src/app/routing/accessPolicy.ts \
  apps/web/src/app/routing/accessPolicy.test.ts \
  apps/web/src/domains/analytics/analyticsModel.ts \
  apps/web/src/domains/analytics/analyticsModel.test.ts
git commit -m "feat(web): define honest analytics projections"
```

---

### Task 8: Replace placeholder Analytics with real history and park comparison

**Files:**
- Modify: `apps/api/src/robopark_api/deps.py`
- Modify: `apps/api/src/robopark_api/routers/dashboard.py`
- Modify: `apps/api/tests/test_dashboard_router.py`
- Create: `apps/web/src/domains/analytics/BlockerFlowPanel.tsx`
- Create: `apps/web/src/domains/analytics/ParkComparisonPanel.tsx`
- Create: `apps/web/src/domains/analytics/AnalyticsPage.tsx`
- Create: `apps/web/src/domains/analytics/AnalyticsPage.test.tsx`
- Create: `apps/web/src/domains/analytics/analytics.css`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Replace: `apps/web/src/pages/Analytics.tsx:1-99`
- Replace: `apps/web/src/pages/Analytics.test.tsx`

**Interfaces:**
- `BlockerFlowPanel({ model, parkName })` renders an accessible SVG plus the same observed values in a table, source, unit, freshness and an upper-bound coverage statement.
- `ParkComparisonPanel({ model })` renders cross-park blocker composition, explicit all-assigned-parks scope and skipped reasons; it never renders `NowReport.totals`.
- `AnalyticsPage` owns `days` URL state and unconditional history/comparison resource hooks; foundation `ParkScopeProvider` remains the sole owner of `park`.
- Both Analytics resource keys include the authenticated user ID. Cached history/comparison may remain visible only for classified `offline`/`timeout`/`server` revalidation failures; `401`/`403` synchronously suppress all protected panels/actions, purge only `analytics:*:${user.id}:` namespaces, and trigger auth/scope recovery.
- Produces `hasAnalyticsScope(user)` without extending the frozen `AccessPrerequisite` union: admin/royal or an actor with `parks.manage` has fleet scope; every other role needs at least one assigned park. `canAccessRoute` applies this one centralized route-specific condition to `analytics`, and `analyticsSectionsFor` reuses the same predicate.
- `/dashboard/history` consumes `nav.analytics` plus the same fail-closed park scope. `/dashboard/summary` remains on `nav.dashboard`; the public history URL and response DTO do not change.

- [ ] **Step 1: Write failing page, API-gating, and route tests**

Create `AnalyticsPage.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { AnalyticsPage } from './AnalyticsPage'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return { ...actual, api: { ...actual.api } }
})

const parks = [
  { id: 7, name: 'Север', tag: 'NORTH' },
  { id: 8, name: 'Юг', tag: 'SOUTH' },
]
const operator: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.analytics'],
  parks,
}

function renderPage(user: User) {
  return render(
    <AuthContext.Provider value={{
      user,
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
    }}>
      <MemoryRouter initialEntries={['/analytics?days=7&park=7']}>
        <ParkScopeProvider><AnalyticsPage /></ParkScopeProvider>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'parks').mockResolvedValue(parks)
  vi.spyOn(api, 'dashboardHistory').mockResolvedValue({
    park_id: 7,
    points: [
      { bucket_start: '2026-09-01T20:00:00Z', arrived_count: 3, departed_count: 1 },
      { bucket_start: '2026-09-02T02:00:00Z', arrived_count: 2, departed_count: 4 },
    ],
  })
  vi.spyOn(api, 'operatorNowReport').mockResolvedValue({
    generated_at: '2026-09-02T12:00:00+03:00',
    scope: 'all',
    totals: { blocker: 99, arrived: 99, done: 99 },
    parks: [
      { park_id: 7, park_name: 'Север', park_tag: 'NORTH', metrics: { open_blockers: 2 } },
      { park_id: 8, park_name: 'Юг', park_tag: 'SOUTH', metrics: { open_blockers: 5 } },
    ],
    skipped_parks: [],
  })
})

describe('AnalyticsPage', () => {
  it('shows observed history and cross-park comparison without overview duplicates', async () => {
    renderPage(operator)
    expect(await screen.findByRole('heading', { name: 'Динамика блокеров' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Сравнение парков' })).toBeInTheDocument()
    expect(screen.getByText(/Получено 2 из максимум 84/i)).toBeInTheDocument()
    expect(screen.getByText(/Источник: история блокеров Robopark/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Сегодня$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Итого$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/SLA/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/повторяем/i)).not.toBeInTheDocument()
    expect(JSON.stringify(document.body.textContent)).not.toContain('99')
    expect(api.dashboardHistory).toHaveBeenCalledWith(7, 7)
    expect(api.operatorNowReport).toHaveBeenCalledWith()
  })

  it('does not call the operator-only comparison API for admins', async () => {
    renderPage({ ...operator, role: 'admin', username: 'admin' })
    await screen.findByRole('heading', { name: 'Динамика блокеров' })
    await waitFor(() => expect(api.dashboardHistory).toHaveBeenCalledWith(7, 7))
    expect(api.operatorNowReport).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Сравнение парков' })).not.toBeInTheDocument()
  })
})
```

Extend this test file with seeded-cache authorization regressions. Import `resourceStore` and `resetCoalescingForTests`, clear both after every test, and let `renderPage` receive an injectable `refreshUser` spy. Seed current-user history and comparison keys plus another synthetic user's same park/range keys. For an explicitly transient offline/timeout/`500` revalidation, assert the current user's chart/table stay mounted with a non-blocking stale warning and retry. Then parameterize `401` and `403`: make one revalidation reject while the other promise remains deliberately deferred, and in that same denial render—before resolving the second promise or waiting for effect-driven invalidation—assert **both** cached panels, table values and actions are absent. After effects settle, assert every current-user `analytics:history:${operator.id}:` / `analytics:comparison:${operator.id}:` key is gone, the other user's entries remain, a full classified `ErrorState` is visible, and `refreshUser` is called exactly once for the denial burst. Repeat with the opposite resource rejecting first. A cold denial must never construct a zero chart or placeholder data.

Add to `routeManifest.test.ts`:

```ts
it('registers one useful Analytics navigation destination', () => {
  const analytics = ROUTE_MANIFEST.filter((route) => route.id === 'analytics')
  expect(analytics).toHaveLength(1)
  expect(analytics[0]).toMatchObject({
    path: '/analytics',
    permission: 'nav.analytics',
    prerequisites: ['password-changed', 'approved'],
    surface: 'shell',
    nav: { group: 'insights', desktopOrder: 10 },
  })
})
```

In `accessPolicy.test.ts`, replace the cumulative Analytics row with the same permission and `mechanicPark: true`, then add this independent scope matrix in addition to the existing role/status/permission/password/mechanic loops:

```ts
it.each([
  { role: 'driver', parks: [north], permissions: ['nav.analytics'], expected: true },
  { role: 'driver', parks: [], permissions: ['nav.analytics'], expected: false },
  { role: 'field_lead', parks: [north], permissions: ['nav.analytics'], expected: true },
  { role: 'field_lead', parks: [], permissions: ['nav.analytics'], expected: false },
  { role: 'field_lead', parks: [], permissions: ['nav.analytics', 'parks.manage'], expected: true },
  { role: 'admin', parks: [], permissions: ['nav.analytics'], expected: true },
  { role: 'royal', parks: [], permissions: ['nav.analytics'], expected: true },
])('analytics scope for $role parks=$parks.length is $expected', ({ expected, ...overrides }) => {
  const candidate = user({ access_status: 'approved', ...overrides })
  expect(canAccessRoute(candidate, 'analytics')).toBe(expected)
  expect(navigationForUser(candidate, 'desktop').some((item) => item.id === 'analytics')).toBe(expected)
  expect(analyticsSectionsFor(candidate).length > 0).toBe(expected)
})
```

Add API RED tests to `test_dashboard_router.py`: seed an approved custom `field_lead` with only `nav.analytics`, assign it to the requested park, and assert `GET /dashboard/history` is `200` while `GET /dashboard/summary` is `403`; an unassigned park is `403`. Seed another approved user with only `nav.dashboard` and assert summary remains `200` for its assigned park but history is `403`. Use real `Role`, catalog `Permission`, `UserPark`, and session helpers rather than editing `user.role` strings in memory.

Replace the old placeholder test in `pages/Analytics.test.tsx`:

```ts
import { describe, expect, it } from 'vitest'
import { AnalyticsPage } from '../domains/analytics/AnalyticsPage'
import { Analytics } from './Analytics'

describe('Analytics compatibility export', () => {
  it('points legacy imports to the real domain page', () => {
    expect(Analytics).toBe(AnalyticsPage)
  })
})
```

- [ ] **Step 2: Run focused tests and confirm RED**

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_dashboard_router.py

cd ../web
npm test -- \
  src/domains/analytics/AnalyticsPage.test.tsx \
  src/pages/Analytics.test.tsx \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts
```

Expected: API permission tests fail against the dashboard-only dependency; web tests fail because the scope prerequisite/domain panels are absent and the compatibility page still contains placeholder KPI content.

- [ ] **Step 3: Align Analytics authorization, then implement an accessible blocker-flow chart**

In `deps.py`, extract the assignment/fleet decision without weakening the existing Overview gate:

```python
def require_scoped_park(
    park_id: int,
    db: Session,
    user: User,
    *,
    permission: str,
) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    rbac.require_approved_permission(db, user, permission)
    if rbac.is_admin_or_royal(user) or rbac.has_permission(
        db, user, rbac.PERMISSION_PARKS_MANAGE
    ):
        return park
    return require_operator_park(park_id, db, user)


def require_dashboard_park(park_id: int, db: Session, user: User) -> Park:
    return require_scoped_park(
        park_id, db, user, permission=rbac.PERMISSION_NAV_DASHBOARD
    )


def require_analytics_park(park_id: int, db: Session, user: User) -> Park:
    return require_scoped_park(
        park_id, db, user, permission=rbac.PERMISSION_NAV_ANALYTICS
    )
```

Change only `dashboard_history` to call `require_analytics_park`; summary keeps `require_dashboard_park`. This preserves 404-before-scope behavior and the assigned-park/fleet rules established in Phase 2.

Keep the Foundation `AccessPrerequisite` union frozen and consume the `hasAnalyticsScope` predicate already produced by Task 7; do not redefine it here. After ordinary permission/prerequisite checks, `canAccessRoute` returns false when `routeId === 'analytics' && !hasAnalyticsScope(user)`. Therefore the same function controls direct-route access, navigation visibility, and Task 7 section availability without introducing a fourth prerequisite value; do not repeat a role allowlist in the page.

Create `BlockerFlowPanel.tsx`:

```tsx
import { StaleBadge } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import { analyticsFreshness, type BlockerFlowModel } from './analyticsModel'

export function BlockerFlowPanel({
  model,
  parkName,
}: {
  model: BlockerFlowModel
  parkName: string
}) {
  const max = Math.max(1, ...model.days.flatMap((day) => [day.arrived, day.departed]))
  const width = 640
  const height = 220
  const slot = width / Math.max(1, model.days.length)
  const net = model.netFlow > 0 ? `+${model.netFlow}` : String(model.netFlow)
  return (
    <Panel
      actions={(
        <StaleBadge
          label="Последний интервал"
          state={analyticsFreshness(model.latestBucketAt)}
          updatedAt={model.latestBucketAt ?? undefined}
        />
      )}
      description={`Парк: ${parkName}. Наблюдаемые двухчасовые интервалы за ${model.rangeDays} дней.`}
      title="Динамика блокеров"
    >
      <p className="rp-analytics-summary">
        Пришло {model.totalArrived}, ушло {model.totalDeparted}; изменение потока {net} {model.unit}.
        Это не текущий остаток блокеров.
      </p>
      <p className="rp-analytics-source">
        Источник: история блокеров Robopark. Единица: {model.unit}.
      </p>
      {model.isPartial && (
        <p className="rp-analytics-coverage" role="status">
          Неполные данные: получено {model.pointCount} из максимум {model.maximumPointCount}
          {' '}двухчасовых интервалов. Отсутствующие интервалы не заменены нулями.
        </p>
      )}
      <svg
        aria-labelledby="blocker-flow-chart-title blocker-flow-chart-desc"
        className="rp-blocker-chart"
        preserveAspectRatio="none"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <title id="blocker-flow-chart-title">Приход и уход блокеров по наблюдаемым дням</title>
        <desc id="blocker-flow-chart-desc">
          {model.days.map((day) =>
            `${day.label}: пришло ${day.arrived}, ушло ${day.departed}`,
          ).join('; ')}
        </desc>
        {model.days.map((day, index) => {
          const x = index * slot + slot * 0.2
          const arrivedHeight = day.arrived / max * (height - 40)
          const departedHeight = day.departed / max * (height - 40)
          return (
            <g key={day.dateKey}>
              <rect
                className="rp-blocker-chart__arrived"
                height={arrivedHeight}
                width={slot * 0.25}
                x={x}
                y={height - 24 - arrivedHeight}
              />
              <rect
                className="rp-blocker-chart__departed"
                height={departedHeight}
                width={slot * 0.25}
                x={x + slot * 0.3}
                y={height - 24 - departedHeight}
              />
              <text textAnchor="middle" x={index * slot + slot / 2} y={height - 6}>
                {day.label}
              </text>
            </g>
          )
        })}
      </svg>
      <div aria-label="Легенда графика" className="rp-chart-legend">
        <span><i data-series="arrived" />Пришло</span>
        <span><i data-series="departed" />Ушло</span>
      </div>
      <table className="rp-analytics-table">
        <caption>Наблюдаемые дневные суммы, {model.unit}</caption>
        <thead><tr><th>Дата</th><th>Пришло</th><th>Ушло</th><th>Интервалов</th></tr></thead>
        <tbody>
          {model.days.map((day) => (
            <tr key={day.dateKey}>
              <th data-label="Дата" scope="row">{day.label}</th>
              <td data-label="Пришло">{day.arrived}</td>
              <td data-label="Ушло">{day.departed}</td>
              <td data-label="Интервалов">{day.bucketCount}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
```

- [ ] **Step 4: Implement all-parks comparison and skipped reasons**

Create `ParkComparisonPanel.tsx`:

```tsx
import { StaleBadge } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import {
  analyticsFreshness,
  type ParkComparisonModel,
} from './analyticsModel'

const value = (metric: number | null) => metric == null ? 'Нет данных' : metric

export function ParkComparisonPanel({ model }: { model: ParkComparisonModel }) {
  return (
    <Panel
      actions={(
        <StaleBadge
          label="Сформировано"
          state={analyticsFreshness(model.generatedAt)}
          updatedAt={model.generatedAt}
        />
      )}
      description="Все назначенные оператору парки; выбор парка в шапке к этому сравнению не применяется."
      title="Сравнение парков"
    >
      <p className="rp-analytics-source">
        Источник: текущий отчёт Tracker. Единица: открытые задачи-блокеры.
      </p>
      <table className="rp-analytics-table rp-analytics-table--parks">
        <caption>Состав текущей blocker-очереди по паркам</caption>
        <thead>
          <tr>
            <th>Парк</th><th>Блокеры</th><th>Бэклог</th>
            <th>В очереди</th><th>Смежники</th><th>Запчасти</th>
          </tr>
        </thead>
        <tbody>
          {model.rows.map((park) => (
            <tr key={park.parkId}>
              <th data-label="Парк" scope="row">{park.parkName} ({park.parkTag})</th>
              <td data-label="Блокеры">{value(park.openBlockers)}</td>
              <td data-label="Бэклог">{value(park.backlog)}</td>
              <td data-label="В очереди">{value(park.queued)}</td>
              <td data-label="Смежники">{value(park.waitingTeam)}</td>
              <td data-label="Запчасти">{value(park.waitingParts)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {model.skipped.length > 0 && (
        <section aria-labelledby="skipped-parks-title" className="rp-skipped-parks">
          <h3 id="skipped-parks-title">Парки без данных</h3>
          <ul>
            {model.skipped.map((park) => (
              <li key={park.parkId}>{park.parkName}: {park.reason}</li>
            ))}
          </ul>
        </section>
      )}
    </Panel>
  )
}
```

- [ ] **Step 5: Implement the controller with endpoint gating and honest empty/error states**

Create `AnalyticsPage.tsx`; both hooks stay above every return:

```tsx
import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type DashboardHistory, type NowReport } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState, StaleBadge } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import {
  analyticsSectionsFor,
  buildBlockerFlow,
  buildParkComparison,
  parseAnalyticsQuery,
  serializeAnalyticsQuery,
  type AnalyticsRange,
} from './analyticsModel'
import { BlockerFlowPanel } from './BlockerFlowPanel'
import { ParkComparisonPanel } from './ParkComparisonPanel'
import './analytics.css'

export function AnalyticsPage() {
  const { user, refreshUser } = useAuth()
  const parkScope = useParkScope()
  const location = useLocation()
  const navigate = useNavigate()
  const query = parseAnalyticsQuery(location.search)
  const sections = user ? analyticsSectionsFor(user) : []
  const historyEnabled = Boolean(user)
    && sections.includes('blocker-flow')
    && parkScope.parkId != null
  const comparisonEnabled = Boolean(user)
    && sections.includes('park-comparison')
  const accessUserId = user?.id ?? -1

  const history = useCachedResource<DashboardHistory>(
    historyEnabled ? `analytics:history:${accessUserId}:${parkScope.parkId}:${query.days}` : '',
    () => api.dashboardHistory(parkScope.parkId as number, query.days),
    { enabled: historyEnabled },
  )
  const comparison = useCachedResource<NowReport>(
    comparisonEnabled ? `analytics:comparison:${accessUserId}:all` : '',
    () => api.operatorNowReport(),
    { enabled: comparisonEnabled },
  )
  const authorizationRecoveryUserId = useRef<number | null>(null)

  const historyError = history.error
    ? classifyApiError(history.error, 'Не удалось загрузить историю блокеров.')
    : null
  const comparisonError = comparison.error
    ? classifyApiError(comparison.error, 'Не удалось сравнить парки.')
    : null
  const authorizationFailure = (
    historyError?.kind === 'unauthorized' || historyError?.kind === 'forbidden'
  )
    ? history.error
    : (
        comparisonError?.kind === 'unauthorized' || comparisonError?.kind === 'forbidden'
      )
      ? comparison.error
      : null
  const authorizationBlocked = authorizationFailure != null
  const canRetainProtectedAnalytics = (failure: DomainError | null) =>
    failure != null && ['offline', 'timeout', 'server'].includes(failure.kind)
  const historyCanRenderData = !authorizationBlocked
    && history.data !== undefined
    && (!historyError || canRetainProtectedAnalytics(historyError))
  const comparisonCanRenderData = !authorizationBlocked
    && comparison.data !== undefined
    && (!comparisonError || canRetainProtectedAnalytics(comparisonError))

  useEffect(() => {
    if (!authorizationFailure) {
      authorizationRecoveryUserId.current = null
      return
    }
    if (authorizationRecoveryUserId.current === accessUserId) return
    authorizationRecoveryUserId.current = accessUserId
    resourceStore.invalidate(`analytics:history:${accessUserId}:`, { prefix: true })
    resourceStore.invalidate(`analytics:comparison:${accessUserId}:`, { prefix: true })
    void refreshUser().catch(() => undefined)
  }, [accessUserId, authorizationFailure, refreshUser])

  if (!user) return <LoadingState label="Загрузка аналитики" variant="page" />

  async function refresh() {
    await Promise.all([
      historyEnabled ? history.refresh() : Promise.resolve(),
      comparisonEnabled ? comparison.refresh() : Promise.resolve(),
    ])
  }

  return (
    <PageLayout
      actions={!authorizationFailure ? (
        <Button
          busy={history.isRevalidating || comparison.isRevalidating}
          leadingIcon="refresh"
          onClick={() => void refresh()}
          type="button"
          variant="secondary"
        >
          Обновить
        </Button>
      ) : undefined}
      description="Тренды и сравнение только по данным, которые уже сохраняет Robopark."
      title="Аналитика"
    >
      {sections.length === 0 && (
        <EmptyState
          description="Для этой роли нет аналитического API с подтверждённым scope."
          icon="analytics"
          title="Доступных аналитических разделов нет"
        />
      )}
      {sections.includes('blocker-flow') && (
        <FormField id="analytics-range" label="Период">
          <select
            id="analytics-range"
            onChange={(event) => {
              const days = Number(event.target.value) as AnalyticsRange
              navigate({
                pathname: location.pathname,
                search: serializeAnalyticsQuery({ days }, location.search),
              }, { replace: true })
            }}
            value={query.days}
          >
            <option value={7}>7 дней</option>
            <option value={14}>14 дней</option>
            <option value={30}>30 дней</option>
          </select>
        </FormField>
      )}
      {sections.includes('blocker-flow') && parkScope.parkId == null && !parkScope.loading && (
        <EmptyState
          description="История всегда относится к конкретному парку."
          icon="analytics"
          title="Нет доступного парка"
        />
      )}
      {historyEnabled && history.isLoading && (
        <LoadingState label="Загрузка истории блокеров" variant="panel" />
      )}
      {historyError && !historyCanRenderData && (
        <ErrorState
          description={historyError.description}
          onRetry={historyError.retryable ? () => void history.refresh() : undefined}
          requestId={historyError.requestId}
          title={historyError.title}
        />
      )}
      {historyError && historyCanRenderData && (
        <div className="rp-analytics-stale-warning" role="alert">
          <StaleBadge state={historyError.kind === 'offline' ? 'offline' : 'stale'} />
          <span>{historyError.description}</span>
          <Button onClick={() => void history.refresh()} type="button" variant="secondary">
            Повторить
          </Button>
        </div>
      )}
      {historyEnabled && historyCanRenderData && history.data?.points.length === 0 && (
        <EmptyState
          description="Сервис ещё не сохранил ни одного двухчасового интервала; нулевой ряд не строится."
          icon="analytics"
          title="Истории за период нет"
        />
      )}
      {historyEnabled && historyCanRenderData && history.data && history.data.points.length > 0 && (
        <BlockerFlowPanel
          model={buildBlockerFlow(history.data, query.days)}
          parkName={parkScope.selectedPark?.name ?? `Парк #${history.data.park_id}`}
        />
      )}
      {comparisonEnabled && comparison.isLoading && (
        <LoadingState label="Сравнение парков" variant="panel" />
      )}
      {comparisonError && !comparisonCanRenderData && (
        <ErrorState
          description={comparisonError.description}
          onRetry={comparisonError.retryable ? () => void comparison.refresh() : undefined}
          requestId={comparisonError.requestId}
          title={comparisonError.title}
        />
      )}
      {comparisonError && comparisonCanRenderData && (
        <div className="rp-analytics-stale-warning" role="alert">
          <StaleBadge state={comparisonError.kind === 'offline' ? 'offline' : 'stale'} />
          <span>{comparisonError.description}</span>
          <Button onClick={() => void comparison.refresh()} type="button" variant="secondary">
            Повторить
          </Button>
        </div>
      )}
      {comparisonEnabled && comparisonCanRenderData && comparison.data && (
        <ParkComparisonPanel model={buildParkComparison(comparison.data)} />
      )}
    </PageLayout>
  )
}
```

Keep both resource hooks and the authorization-recovery effect above every return. `authorizationBlocked` is global to the page: the first history **or** comparison `401`/`403` makes both `historyCanRenderData` and `comparisonCanRenderData` false in the same render, so no sibling cache remains painted while cleanup is pending. Rendering is fail-closed immediately and never waits for the effect that clears persisted entries. Key the recovery effect by the stable raw resource error and coalesce a simultaneous history/comparison denial into one recovery call. A transient failure may reuse only the same authenticated user's cached payload and always carries a visible stale/offline warning.

- [ ] **Step 6: Replace the Foundation Analytics entry and compatibility export**

Foundation already defines `analytics` in `AppRouteId`, `ROUTE_MANIFEST`, and `ROUTE_ELEMENTS`. Replace its manifest record in place with the following metadata; do not extend the union or append a duplicate item:

```ts
  {
    id: 'analytics',
    path: '/analytics',
    label: 'Аналитика',
    icon: 'analytics',
    permission: 'nav.analytics',
    prerequisites: ['password-changed', 'approved'],
    surface: 'shell',
    nav: {
      group: 'insights',
      desktopOrder: 10,
      mobilePriority: { operator: 40, admin: 30, royal: 30, mechanic: 40 },
    },
  },
```

Import `AnalyticsPage` in `AppRouter.tsx` and extend `ROUTE_ELEMENTS`:

```tsx
  analytics: <AnalyticsPage />,
```

Replace `pages/Analytics.tsx` completely:

```ts
export { AnalyticsPage as Analytics } from '../domains/analytics/AnalyticsPage'
```

Do not import `OperatorNowReport`, `dashboardSummary`, `SkeletonKpi`, or the old placeholder copy. Leave `pages/OperatorNowReport.tsx` untouched as Phase 5 dead-code cleanup.

- [ ] **Step 7: Add responsive chart and card-table styles using frozen tokens**

Create `analytics.css`:

```css
.rp-analytics-summary {
  font-size: 1.125rem;
  color: var(--rp-text);
}

.rp-analytics-source {
  color: var(--rp-text-muted);
  font-size: 0.875rem;
}

.rp-analytics-coverage,
.rp-skipped-parks {
  padding: var(--rp-space-3);
  border: 1px solid var(--rp-warning);
  border-radius: var(--rp-radius-control);
  background: var(--rp-warning-surface);
}

.rp-analytics-stale-warning {
  display: flex;
  gap: var(--rp-space-2);
  align-items: center;
  flex-wrap: wrap;
  padding: var(--rp-space-3);
  color: var(--rp-warning);
  border: 1px solid currentcolor;
  border-radius: var(--rp-radius-control);
  background: var(--rp-warning-surface);
}

.rp-analytics-stale-warning > span:not([class]) {
  flex: 1 1 14rem;
}

.rp-blocker-chart {
  display: block;
  width: 100%;
  min-height: 13.75rem;
  color: var(--rp-text-muted);
}

.rp-blocker-chart__arrived {
  fill: var(--rp-chart-1);
}

.rp-blocker-chart__departed {
  fill: var(--rp-chart-2);
}

.rp-blocker-chart text {
  fill: currentcolor;
  font-size: 0.75rem;
}

.rp-chart-legend {
  display: flex;
  gap: var(--rp-space-4);
  flex-wrap: wrap;
}

.rp-chart-legend span {
  display: inline-flex;
  gap: var(--rp-space-2);
  align-items: center;
}

.rp-chart-legend i {
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  background: var(--rp-chart-1);
}

.rp-chart-legend i[data-series='departed'] {
  background: var(--rp-chart-2);
}

.rp-analytics-table {
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}

.rp-analytics-table th,
.rp-analytics-table td {
  padding: var(--rp-space-2);
  text-align: start;
  border-bottom: 1px solid var(--rp-border);
}

@media (max-width: 599px) {
  .rp-blocker-chart {
    min-height: 11rem;
  }

  .rp-analytics-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }

  .rp-analytics-table,
  .rp-analytics-table tbody,
  .rp-analytics-table tr,
  .rp-analytics-table th,
  .rp-analytics-table td {
    display: block;
    width: 100%;
  }

  .rp-analytics-table tr {
    margin-block: var(--rp-space-3);
    padding: var(--rp-space-3);
    border: 1px solid var(--rp-border);
    border-radius: var(--rp-radius-panel);
    background: var(--rp-surface);
  }

  .rp-analytics-table th,
  .rp-analytics-table td {
    display: flex;
    gap: var(--rp-space-3);
    justify-content: space-between;
  }

  .rp-analytics-table [data-label]::before {
    content: attr(data-label);
    color: var(--rp-text-muted);
    font-weight: 400;
  }
}
```

- [ ] **Step 8: Run GREEN and Analytics regressions**

```sh
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_dashboard_router.py tests/test_rbac_seed.py
uv run --frozen --extra dev ruff check src/robopark_api/deps.py \
  src/robopark_api/routers/dashboard.py tests/test_dashboard_router.py

cd ../web
npm test -- \
  src/domains/analytics/analyticsModel.test.ts \
  src/domains/analytics/AnalyticsPage.test.tsx \
  src/pages/Analytics.test.tsx \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts
npm run check-nav
npm run lint
npm run build
git diff --check
```

Expected: history uses `nav.analytics` with fail-closed park scope while summary remains on `nav.dashboard`; both real sections render for a multi-park operator; assigned-park driver/custom roles receive useful blocker history; users without useful scope see no Analytics destination; admin never calls the operator endpoint; transient cached data is visibly stale, while a `401`/`403` reveals no cached Analytics payload and purges only the denied user's namespace; the placeholder and duplicate current-day totals are gone; and exactly one Analytics nav destination remains.

- [ ] **Step 9: Commit**

```sh
git add \
  apps/api/src/robopark_api/deps.py \
  apps/api/src/robopark_api/routers/dashboard.py \
  apps/api/tests/test_dashboard_router.py \
  apps/web/src/domains/analytics/BlockerFlowPanel.tsx \
  apps/web/src/domains/analytics/ParkComparisonPanel.tsx \
  apps/web/src/domains/analytics/AnalyticsPage.tsx \
  apps/web/src/domains/analytics/AnalyticsPage.test.tsx \
  apps/web/src/domains/analytics/analytics.css \
  apps/web/src/app/routing/routeManifest.ts \
  apps/web/src/app/routing/accessPolicy.ts \
  apps/web/src/app/routing/AppRouter.tsx \
  apps/web/src/app/routing/routeManifest.test.ts \
  apps/web/src/app/routing/accessPolicy.test.ts \
  apps/web/src/pages/Analytics.tsx \
  apps/web/src/pages/Analytics.test.tsx
git commit -m "feat(web): rebuild analytics from real data"
```

---

### Task 9: Lock Analytics E2E evidence and run the Phase 3 gate

**Files:**
- Create: `apps/web/e2e/analytics/fixtures.ts`
- Create: `apps/web/e2e/analytics/analytics.spec.ts`
- Create: `apps/web/e2e/analytics/analytics.visual.spec.ts`
- Create: `apps/web/e2e/analytics/analytics.visual.spec.ts-snapshots/*.png`

**Interfaces:**
- Consumes unchanged foundation `installMockApi` and `assertNoSeriousA11yViolations`.
- Custom routes are exactly `GET /api/dashboard/history` and `GET /api/operator/now-report`; query parameters are asserted inside handlers.
- The final gate combines deterministic frontend tests with existing backend contract tests; it never starts or reads a production/demo database.

- [ ] **Step 1: Add deterministic Analytics route fixtures**

Create `apps/web/e2e/analytics/fixtures.ts`:

```ts
import type { DashboardHistory, NowReport, Park, User } from '../../src/api'
import type { MockRoute } from '../support/mockApi'

export const analyticsParks: Park[] = [
  { id: 7, name: 'Север', tag: 'NORTH' },
  { id: 8, name: 'Юг', tag: 'SOUTH' },
]
export const analyticsOperator: User = {
  id: 2,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['nav.analytics'],
  parks: analyticsParks,
}
export const analyticsHistory: DashboardHistory = {
  park_id: 7,
  points: [
    { bucket_start: '2026-08-31T20:00:00Z', arrived_count: 2, departed_count: 1 },
    { bucket_start: '2026-09-01T02:00:00Z', arrived_count: 4, departed_count: 3 },
    { bucket_start: '2026-09-02T02:00:00Z', arrived_count: 1, departed_count: 2 },
  ],
}
export const analyticsComparison: NowReport = {
  generated_at: '2026-09-02T12:00:00+03:00',
  scope: 'all',
  totals: { blocker: 101, arrived: 202, done: 303 },
  parks: [
    {
      park_id: 7,
      park_name: 'Север',
      park_tag: 'NORTH',
      metrics: { open_blockers: 2, backlog: 1, queued: 1, waiting_parts: 0 },
    },
    {
      park_id: 8,
      park_name: 'Юг',
      park_tag: 'SOUTH',
      metrics: { open_blockers: 5, backlog: 3, waiting_team: 2 },
    },
  ],
  skipped_parks: [{
    park_id: 9,
    park_name: 'Запад',
    reason: 'no_tracker_queue',
  }],
}

export function analyticsRoutes(calls = { history: 0, comparison: 0 }) {
  const routes: MockRoute[] = [
    {
      method: 'GET',
      path: '/api/dashboard/history',
      handler: (request) => {
        calls.history += 1
        const query = new URL(request.url).searchParams
        if (query.get('park_id') !== '7' || query.get('days') !== '7') {
          return { status: 400, json: { detail: 'unexpected_history_scope' } }
        }
        return { json: analyticsHistory }
      },
    },
    {
      method: 'GET',
      path: '/api/operator/now-report',
      handler: (request) => {
        calls.comparison += 1
        if (new URL(request.url).searchParams.has('park_id')) {
          return { status: 400, json: { detail: 'comparison_must_use_all_parks' } }
        }
        return { json: analyticsComparison }
      },
    },
  ]
  return { calls, routes }
}
```

- [ ] **Step 2: Add real-data, role-gating, no-placeholder, overflow, and a11y journeys**

Create `analytics.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installMockApi } from '../support/mockApi'
import {
  analyticsOperator,
  analyticsParks,
  analyticsRoutes,
} from './fixtures'

test('operator sees observed trend and all-parks comparison only', async ({ page }) => {
  const mock = analyticsRoutes()
  await installMockApi(page, {
    user: analyticsOperator,
    parks: analyticsParks,
    routes: mock.routes,
  })
  await page.goto('/analytics?days=7&park=7')

  await expect(page.getByRole('heading', { name: 'Динамика блокеров' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Сравнение парков' })).toBeVisible()
  await expect(page.getByText(/получено 3 из максимум 84/i)).toBeVisible()
  await expect(page.getByText(/выбор парка в шапке.*не применяется/i)).toBeVisible()
  await expect(page.getByText('Запад: Не настроена очередь Tracker')).toBeVisible()
  await expect(page.getByText(/^Сегодня$/)).toHaveCount(0)
  await expect(page.getByText(/^Итого$/)).toHaveCount(0)
  await expect(page.getByText(/SLA/i)).toHaveCount(0)
  await expect(page.getByText(/повторяем/i)).toHaveCount(0)
  await expect(page.getByText('101')).toHaveCount(0)
  expect(mock.calls.history).toBe(1)
  expect(mock.calls.comparison).toBe(1)
})

test('admin receives history but never invokes operator-only comparison', async ({ page }) => {
  const mock = analyticsRoutes()
  await installMockApi(page, {
    user: { ...analyticsOperator, username: 'admin', role: 'admin' },
    parks: analyticsParks,
    routes: mock.routes,
  })
  await page.goto('/analytics?days=7&park=7')

  await expect(page.getByRole('heading', { name: 'Динамика блокеров' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Сравнение парков' })).toHaveCount(0)
  expect(mock.calls.history).toBe(1)
  expect(mock.calls.comparison).toBe(0)
})

test('analytics is a semantic card layout without 320 px overflow', async ({ page }) => {
  const mock = analyticsRoutes()
  await installMockApi(page, {
    user: analyticsOperator,
    parks: analyticsParks,
    routes: mock.routes,
  })
  await page.setViewportSize({ width: 320, height: 720 })
  await page.goto('/analytics?days=7&park=7')
  await expect(page.getByRole('img', {
    name: /Приход и уход блокеров по наблюдаемым дням/,
  })).toBeVisible()
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
  await assertNoSeriousA11yViolations(page)
})
```

Add a fourth stateful journey. Let the first history/comparison responses succeed, then revoke the current user's park/permission in the fixture and return `403` on refresh. Seed a same-key Analytics cache record for synthetic user `99` before the refresh. After clicking `Обновить`, assert every current-user chart/table value and operation is absent, the classified access error is visible, the persisted current-user Analytics keys are removed, and user `99`'s keys remain. Repeat the revalidation once with an explicit transient `500` control and assert the previous payload remains only with the stale warning and retry action.

- [ ] **Step 3: Run the journey suite**

```sh
cd apps/web
npm run test:e2e -- e2e/analytics/analytics.spec.ts --project=chromium
```

Expected: all four tests pass; the comparison mock observes no `park_id`, the admin test records zero comparison calls, authorization revocation leaves no current-user cached Analytics payload while a transient failure stays visibly stale, and axe reports no applicable WCAG A/AA violation.

- [ ] **Step 4: Add and approve both-theme snapshots at all required widths**

Create `analytics.visual.spec.ts`:

```ts
import { expect, test } from '@playwright/test'
import { installMockApi } from '../support/mockApi'
import {
  analyticsOperator,
  analyticsParks,
  analyticsRoutes,
} from './fixtures'

const heights: Record<number, number> = {
  320: 720,
  390: 844,
  768: 1024,
  1024: 768,
  1440: 1000,
}

for (const theme of ['light', 'dark'] as const) {
  for (const width of [320, 390, 768, 1024, 1440] as const) {
    test(`analytics ${theme} at ${width}px`, async ({ page }) => {
      const mock = analyticsRoutes()
      await page.addInitScript((preference) => {
        localStorage.setItem('robopark-theme', preference)
      }, theme)
      await installMockApi(page, {
        user: analyticsOperator,
        parks: analyticsParks,
        routes: mock.routes,
      })
      await page.setViewportSize({ width, height: heights[width] })
      await page.goto('/analytics?days=7&park=7')
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.getByRole('heading', { name: 'Динамика блокеров' })).toBeVisible()
      await expect(page).toHaveScreenshot(`analytics-${theme}-${width}.png`, {
        animations: 'disabled',
        fullPage: true,
      })
    })
  }
}
```

Run once for the required missing-baseline RED, inspect every diff for readable chart labels and semantic mobile cards, update, then rerun:

```sh
cd apps/web
npm run test:e2e:linux -- e2e/analytics/analytics.visual.spec.ts --project=chromium
npm run test:e2e:update:linux -- e2e/analytics/analytics.visual.spec.ts --project=chromium
npm run test:e2e:linux -- e2e/analytics/analytics.visual.spec.ts --project=chromium
```

Expected: first run fails only for ten absent baselines; after review/update, all ten light/dark snapshots pass.

- [ ] **Step 5: Run the complete deterministic Phase 3 verification gate**

```sh
cd apps/web
npm test
npm run check-nav
npm run lint
npm run build
npm run test:e2e:linux -- e2e/reports e2e/analytics --project=chromium
cd ../api
uv run --frozen alembic heads
uv run --frozen --extra dev pytest -q \
  tests/test_reports.py \
  tests/test_report_attachments.py \
  tests/test_dashboard_router.py \
  tests/test_operator_report.py
cd ../..
./scripts/verify.sh web
git diff --check
git status --short
```

Expected: all unit/component/E2E and existing backend contract tests pass; `alembic heads` prints exactly `0016_report_idempotency (head)`; build/nav checks pass; `git diff --check` is silent; status contains only reviewed source, tests, and snapshot assets from Tasks 1–9.

- [ ] **Step 6: Commit E2E evidence**

```sh
git add \
  apps/web/e2e/analytics/fixtures.ts \
  apps/web/e2e/analytics/analytics.spec.ts \
  apps/web/e2e/analytics/analytics.visual.spec.ts \
  apps/web/e2e/analytics/analytics.visual.spec.ts-snapshots
git commit -m "test(web): cover real analytics journeys"
```

---

## Phase 3 Definition of Done

- Canonical `/reports`, `/reports/new`, `/reports/:reportId`, and `/analytics` routes are in the single foundation manifest/router; only list and Analytics create navigation items.
- Report workspace defaults to inbox for resolvers, preserves supported filters and park scope in the URL, keeps a persistent desktop detail area, and becomes sequential on phone.
- Report inbox/actions/badges use effective permissions plus assigned/fleet scope; system roles retain behavior and capable custom roles are neither over-scoped nor silently excluded.
- Existing attachment upload/download is visible; camera and file affordances are separate; all three server attachment kinds render; the one-`device_photo` limit is respected.
- Draft text is live-session user-scoped, versioned, seven-day expiring, recoverable after create failure, and checkpointed after create success; all report-draft/recent-robots-v2 prefixes are purged before login and on logout/refresh-401 so a recreated account with the same numeric ID cannot restore stale browser data, while ordinary reload/offline retains the current session's draft. A persisted per-author idempotency key makes a commit-plus-lost-response retry resolve to exactly one report, no binary file is persisted, and attachment retry cannot duplicate the report.
- Cached report list/detail payloads are user-scoped and survive only transient offline/timeout/server revalidation failures; `401`/`403` suppress and purge the current user's protected cache immediately, preserve other users' namespaces, and trigger auth/scope recovery.
- Detail shows only persisted author id/target role/lifecycle fields, explicitly labels its derived timeline, and offers only server-supported return/done/escalate transitions.
- Analytics contains only observed `/dashboard/history` trend and eligible operator all-park comparison. Overview totals, SLA, assignee, recurring-failure promises, placeholder cards, and invented zero buckets are absent.
- Analytics cache is user-scoped; only offline/timeout/server revalidation may keep it visible with a stale warning, while `401`/`403` synchronously suppress and purge the denied user's history/comparison without touching another user's namespace and trigger auth/scope recovery.
- Every graph has a visible text summary, unit, source, freshness timestamp, partial-data statement, and tabular equivalent.
- Both themes and 320/390/768/1024/1440 widths have reviewed Playwright evidence; 320 px overflow and every applicable WCAG A/AA axe violation are covered.
- Focused frontend tests, full web verification, nav parity, build, deterministic E2E, and backend report/attachment/history/operator-report contract tests pass.
