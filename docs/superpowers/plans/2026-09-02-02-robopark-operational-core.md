# Robopark Operational Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пересобрать операционное ядро Robopark: ролевой «Обзор», единое рабочее пространство задач, поиск и карточку робота, пользовательскую «Проверку робота», восстанавливаемое URL-состояние и полноценное адаптивное поведение на ПК и телефоне.

**Architecture:** Этап 2 выполняется поверх завершённого Foundation-плана и расширяет его `RouteManifest`, `AccessPolicy`, адаптивный `AppShell` и design-system primitives. Существующие FastAPI-контракты Tracker и `/emergency/*` сохраняются; в них добавляются только обратносуместимые metadata (`generated_at`, `observed_at`, issue capabilities, sort), а новый React-код размещается по доменам `shift`, `work` и `robots`. Legacy URL остаются входными compatibility routes, но пользовательские тексты и канонические ссылки используют только «Проверка робота» и `/robots/:vin/check`.

**Tech Stack:** Python 3.12.13, FastAPI, Pydantic 2, pytest, React 19, TypeScript 6, React Router 7, Vitest 4, Testing Library, Playwright, `@axe-core/playwright`, Lucide SVG icons, Leaflet, CSS custom properties.

**Spec:** [`docs/superpowers/specs/2026-09-02-robopark-product-redesign-design.md`](../specs/2026-09-02-robopark-product-redesign-design.md)

**Depends on:** [`docs/superpowers/plans/2026-09-02-01-robopark-foundation.md`](2026-09-02-01-robopark-foundation.md) — выполнить полностью и получить зелёные Foundation gates до Task 1 этого плана.

## Global Constraints

- Выполнять задачи строго по порядку после полного завершения Foundation-плана; не копировать его primitives и не возвращать legacy `AppShell`, `nav.ts` или разрозненные route guards.
- Backend остаётся источником истины для scope и действий. `RouteManifest` управляет только представлением; клиент никогда не ослабляет проверки `/tracker/*`, `/dashboard/*` или `/emergency/*`.
- Не переименовывать технические API paths, Python modules, permission keys и integration settings с `emergency`: публичные `/emergency/*` временно остаются совместимыми. Во всех новых пользовательских текстах использовать только «Проверка робота».
- Не менять существующие response fields и семантику успешных ответов; новые API fields обязательны, additive и покрыты contract tests.
- Не показывать несуществующие данные: в текущем API нет подтверждённой истории событий робота, фактического парка робота, source telemetry timestamp или персональной фотографии выбранного VIN. Этап 2 показывает связанные задачи, явно подписанный scope выбранного парка, серверное время наблюдения `observed_at` и предоставленные владельцем общие иллюстрации модели с нейтральной SVG-схемой при недоступном изображении. История событий и персональные фотографии не имитируются.
- `/dashboard/history` и исторические графики не входят в этот этап: ими целиком владеет Phase 3 Analytics. «Обзор» использует только текущий `/dashboard/summary` и операционную очередь.
- Канонические UI routes этого этапа: `/overview`, `/work`, `/work/:issueKey`, `/robots`, `/robots/:vin`, `/robots/:vin/check`. Старые `/dashboard`, `/tasks`, `/robots/search`, `/emergency?q=...&tab=...` обязаны оставаться рабочими redirects.
- Выбранные фильтры, sort, page, task key, VIN и вкладка проверки восстанавливаются из URL. Local/session storage допустим только для темы, user-scoped recent robots и user-scoped list scroll position; он не является источником route state.
- Numeric `user.id` scopes reads during a live session but is not a durable identity boundary: protected draft/recent prefixes are cleared globally before login and on fail-closed session termination so a recreated account cannot inherit stale browser data.
- Ролевой приоритет не урезает разрешённые функции: механик полноценно работает на ПК, оператор/администратор — на телефоне, водитель получает `/overview`, `/robots` и `/robots/:vin/check` без Tracker ticket data.
- Адаптивные границы дословно из design spec: compact phone `<=599px`, phone/tablet `600–899px`, split tablet `900–1199px`, desktop `>=1200px`. На `320px` основной layout не имеет горизонтального scroll; touch target не меньше `44×44px`; phone всегда использует комфортную плотность.
- Светлая, тёмная и системная темы приходят из Foundation `ThemeProvider`; domain CSS использует только семантические tokens, не raw hex и не отдельные theme branches.
- Статус всегда имеет текст/иконку, не только цвет. Focus indicator не слабее `2px`; tabs реализуют `tablist/tab/tabpanel`, roving focus, ArrowLeft/ArrowRight/Home/End; loading/success используют `aria-live`, ошибки действий — `role="alert"`.
- Polling проверки робота не использует `setInterval`: он останавливается при `document.hidden` или отсутствии сети, делает exponential backoff `2500 → 5000 → 10000 → 20000 → 30000ms`, сбрасывается после успеха и всегда допускает ручное обновление.
- Сканирование запускается только явным действием пользователя и только при наличии `BarcodeDetector` и `getUserMedia`; manual input всегда доступен. Камера и tracks обязательно закрываются при результате, отмене и unmount.
- Не читать runtime database, integration credentials, `.env`, пользовательские payload или живые данные. API tests работают только с временной БД и фиктивными upstream payload; browser tests — только через deterministic `installMockApi`.
- Каждое поведенческое изменение выполняется TDD: сначала новый тест и подтверждённый RED по указанной причине, затем минимальная реализация, затем GREEN и regression set.
- Каждый task заканчивается `git diff --check`, проверкой `git diff --name-only` и отдельным коммитом. После scoped `git add`, но до каждого `git commit`, обязательно выполнить `git diff --cached --check`, сверить `git diff --cached --name-only` с точным allowlist из `Files`/намеренных snapshots текущего task и остановиться при любом заранее staged, несвязанном, runtime, secret, database, log, trace или generated-download path; чужой index не очищать и не перезаписывать.
- Не удалять legacy страницы/components/CSS в этом этапе, кроме явно перечисленных ссылок и route registrations. Физическая очистка мёртвого кода относится к Phase 5.

## Согласованное дополнение: фотографии, 2026-09-03

Владелец предоставил шесть PNG и явно согласовал интеграцию: изометрия в карточке, шесть выбираемых ракурсов в проверке, точные отметки известных неисправностей колёс. Это дополнение заменяет требования «no photo», «neutral icon only» и «code-native SVG only» в иллюстративных шагах Tasks 7–8 ниже; остальные ограничения достоверности, scope, TDD и доступности сохраняются.

- Task 7 дополнительно создаёт `apps/web/src/assets/robots/{top,rear,left,front,right,isometric}.png` как неизменённые копии исходников и `apps/web/src/domains/robots/robotPhotos.ts` с id/title/src/width/height. IdentityCard показывает только изометрию, подпись «Иллюстрация модели», реальный VIN отдельно и исходную нейтральную иконку при ошибке загрузки. Регрессии остаются в `RobotDetailView.test.tsx`.
- Task 8 использует эти assets/metadata и дополнительно создаёт `robotPhotoHotspots.ts`/`robotPhotoHotspots.test.ts` в домене robots. Основной вид сверху показывает все шесть колёс. У каждого ракурса свои координаты видимых узлов; спереди стороны зеркальны экрану, сзади — нет. Скрытые узлы не отмечаются. Изометрия при недостаточном месте может показывать текст ошибок и действие «Показать колёса сверху».
- Коды `fl/ml/rl/fr/mr/rr` сопоставляются с конкретными колёсами. `body` — непривязанная неисправность колёс, не кузов; неизвестные ошибки остаются текстом. Камеры и датчики не локализуются без подтверждённого соответствия кода узлу.
- Загружается выбранный ракурс; исходники не перерисовываются. Фото и overlay имеют одну координатную систему. Кликабельные отметки не перекрываются, имеют размер не менее 44×44px и текстовое/клавиатурное представление. При ошибке фото доступна нейтральная схема, данные не исчезают.
- Task 10 проверяет переключение ракурсов, загрузку только выбранного изображения, привязку неисправностей, отсутствие перекрытия целей на телефоне, две темы и fallback. Точные карты ракурсов покрыты также unit-тестами Task 8.

## Foundation Interfaces Consumed Unchanged

- `apps/web/src/app/routing/routeManifest.ts`: `AppRouteId`, `UserRole`, `isSystemUserRole`, `NavGroup`, `NavSurface`, `RouteManifestItem`, `NavigationItem`, `ROUTE_MANIFEST`; raw API/custom-role slugs remain `string`.
- `apps/web/src/app/routing/accessPolicy.ts`: `AccessUser`, `canAccessRoute(user, routeId)`, `landingPathForUser(user)`, `navigationForUser(user, surface)`.
- `apps/web/src/app/routing/AppRouter.tsx`: `ROUTE_ELEMENTS: Record<AppRouteId, ReactElement>` and manifest-driven `RouteGate`.
- `apps/web/src/design-system/icons/Icon.tsx`: `IconName`, `Icon`; Phase 2 uses `overview`, `work`, `robot`, `robot-check`, `search`, `scan`, `refresh`, `warning`, `critical`, `success`, `info`, `offline`, `camera`, `filter`, `clock`, `assignee`, `back`.
- `apps/web/src/design-system/actions/Button.tsx`: `Button`, `ButtonProps`, `IconButton`.
- `apps/web/src/design-system/status/StatusBadge.tsx`: `StatusTone`, `StatusBadge`.
- `apps/web/src/design-system/layout/PageLayout.tsx`: `PageLayout`, `Panel`.
- `apps/web/src/design-system/forms/FormField.tsx`: `FormField`.
- `apps/web/src/design-system/feedback/AsyncState.tsx`: `LoadingState`, `EmptyState`, `ErrorState`, `Freshness`, `StaleBadge`.
- `apps/web/src/design-system/overlays/Dialog.tsx`: `Dialog`; `BottomSheet.tsx`: `BottomSheet`; `ConfirmDialog.tsx`: `ConfirmDialog`.
- `ConfirmDialog` exact controlled props: `{ open; onOpenChange; title; description; confirmLabel; cancelLabel?; tone?; confirmationPhrase?; pending?; error?; onConfirm }`; the owner closes it only after a successful mutation.
- `apps/web/src/app/park/parkScope.ts`: `PARK_QUERY_KEY = 'park'`, `ParkScopeValue`, `useParkScope`; `ParkScopeValue` contains `parkId`, `selectedPark`, `parks`, `loading`, `locked`, `setParkId`, and `refreshParks`.
- `apps/web/src/app/shell/AppShell.tsx`: manifest navigation and `<Outlet>`.
- `apps/web/e2e/support/mockApi.ts`: `MockResponse`, `MockRouteHandler`, `MockRoute`, `MockApiOptions`, `installMockApi(page, options)`; custom `routes` have precedence and paths include `/api`.
- `apps/web/e2e/support/assertA11y.ts`: `assertNoSeriousA11yViolations(page)`.
- Foundation theme contract: key `robopark-theme`, preference `system | light | dark`, resolved `document.documentElement.dataset.theme`, accessible picker label `Тема оформления`.
- Foundation domain-style tokens used here: `--rp-canvas`, `--rp-surface`, `--rp-surface-elevated`, `--rp-surface-sunken`, `--rp-text`, `--rp-text-muted`, `--rp-border`, `--rp-action`, `--rp-action-on`, `--rp-critical`, `--rp-critical-on`, `--rp-warning`, `--rp-success`, `--rp-info`, `--rp-focus`, status-surface tokens, `--rp-overlay`, `--rp-shadow-panel`, spacing/radius/motion tokens. No Phase 2 stylesheet defines a second token set.

## File and Delivery Map

1. Shared error/network and protected-browser-storage contracts used by all later UI domains.
2. Operational API metadata and driver navigation capabilities.
3. Typed `/work` URL state, pagination and scroll restoration.
4. Capability-driven `IssueWorkbench` for desktop split-view and mobile sequential flow.
5. Role-aware `OverviewPage` without duplicated Analytics history.
6. User-scoped `RobotResolver`, recent robots and progressive camera scanner.
7. Robot detail view composed only from confirmed current APIs.
8. Visibility-aware `RobotCheckWorkspace` with accessible URL-addressed tabs.
9. Manifest registration, canonical links, copy migration and lossless legacy redirects.
10. Deterministic role journeys, accessibility, responsive and visual regression gates.

---

### Task 1: Establish shared API-error, browser-connectivity and protected-storage contracts

**Files:**

- Modify: `apps/web/src/api.ts:381-447`
- Create: `apps/web/src/api.test.ts`
- Create: `apps/web/src/shared/api/classifyApiError.ts`
- Create: `apps/web/src/shared/api/classifyApiError.test.ts`
- Create: `apps/web/src/shared/browser/useOnlineStatus.ts`
- Create: `apps/web/src/shared/browser/useOnlineStatus.test.tsx`
- Create: `apps/web/src/shared/auth/protectedBrowserStorage.ts`
- Create: `apps/web/src/shared/auth/protectedBrowserStorage.test.ts`
- Modify: `apps/web/src/auth.tsx`
- Create: `apps/web/src/auth.test.tsx`
- Modify: `apps/web/src/i18n/errors.ts`
- Modify: `apps/web/src/i18n/errors.test.ts`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**

- Consumes: `ApiError`, `mapApiError(error, fallback)` and browser `online`/`offline` events.
- Produces: `ApiError.requestId?: string`, populated from response header `X-Request-ID` when present, plus `ApiTimeoutError.timeoutMs` for a locally cancelled stalled request.
- Produces: `DomainErrorKind = 'offline' | 'timeout' | 'unauthorized' | 'forbidden' | 'not-found' | 'conflict' | 'configuration' | 'server' | 'unknown'`; `DomainError = { kind; title; description; retryable; requestId? }`; `classifyApiError(error, fallback): DomainError`.
- Produces: `useOnlineStatus(): boolean`; the hook returns `navigator.onLine` and updates on window connectivity events.
- Produces: `clearProtectedBrowserStorage(storage?: Storage): void` plus canonical `RECENT_ROBOTS_V2_STORAGE_PREFIX = 'robopark.recentRobots.v2.'` and `REPORT_DRAFT_STORAGE_PREFIX = 'robopark:report-draft:'`. It removes every user/version key under those protected prefixes, never theme/density or unrelated keys, and does not depend on numeric user ID uniqueness.
- Produces: fail-closed `AuthProvider.login`/`refreshUser`/`logout`: before every login attempt, on a `401` refresh, and in every logout `finally`, clear `resourceStore`, all protected browser-storage namespaces, and in-memory `user` even when the network request fails. Protected domains use `refreshUser` after an authorization denial instead of retaining a stale identity. Successful refresh/reload and non-401 offline/timeout/server failures do not clear persisted drafts/recents.

- [ ] **Step 1: Write RED tests for request ID and error classes**

Create `apps/web/src/api.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError, api } from './api'

describe('API transport metadata', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('copies X-Request-ID into ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: 'tracker_upstream_error' }), {
          status: 502,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': 'req-42',
          },
        }),
      ),
    )

    await expect(api.trackerIssue('ROBOPARK-42')).rejects.toMatchObject<ApiError>({
      status: 502,
      detail: 'tracker_upstream_error',
      requestId: 'req-42',
    })
  })

  it('cancels a stalled JSON request at the transport deadline', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        )
      }),
    ))

    const request = expect(api.trackerIssue('ROBOPARK-42')).rejects.toMatchObject<ApiTimeoutError>({
      name: 'ApiTimeoutError',
      timeoutMs: 30_000,
    })
    await vi.advanceTimersByTimeAsync(30_000)
    await request
  })
})
```

Create `apps/web/src/shared/api/classifyApiError.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { ApiError, ApiTimeoutError } from '../../api'
import { classifyApiError } from './classifyApiError'

describe('classifyApiError', () => {
  it.each([
    [401, null, 'unauthorized', 'Сессия истекла', false],
    [403, null, 'forbidden', 'Нет доступа', false],
    [404, null, 'not-found', 'Не найдено', false],
    [409, null, 'conflict', 'Данные изменились', true],
    [503, 'tracker_token_not_configured', 'configuration', 'Требуется настройка', false],
    [403, 'emergency_cookie_invalid', 'configuration', 'Требуется настройка', false],
    [503, null, 'server', 'Сервис временно недоступен', true],
    [503, 'temporary_upstream_not_configured', 'server', 'Сервис временно недоступен', true],
    [502, null, 'server', 'Сервис временно недоступен', true],
    [504, null, 'server', 'Сервис временно недоступен', true],
    [422, null, 'unknown', 'Не удалось выполнить действие', false],
  ] as const)('maps HTTP %s to %s', (status, detail, kind, title, retryable) => {
    const result = classifyApiError(new ApiError(status, detail, 'req-7'), 'Не удалось загрузить')
    expect(result).toMatchObject({
      kind,
      title,
      retryable,
      requestId: 'req-7',
    })
    expect(result.description.length).toBeGreaterThan(0)
  })

  it('recognizes a known not-configured detail regardless of non-5xx status', () => {
    expect(classifyApiError(new ApiError(409, 'emergency_cookie_not_configured'), 'Нужна настройка')).toMatchObject({
      kind: 'configuration',
      title: 'Требуется настройка',
      retryable: false,
    })
  })

  it('classifies a fetch TypeError as an offline failure', () => {
    expect(classifyApiError(new TypeError('Failed to fetch'), 'Не удалось загрузить')).toEqual({
      kind: 'offline',
      title: 'Нет сети',
      description: 'Проверьте подключение и повторите действие.',
      retryable: true,
    })
  })

  it('keeps a locally enforced timeout distinct from offline and server errors', () => {
    expect(classifyApiError(new ApiTimeoutError(30_000), 'Не удалось загрузить')).toEqual({
      kind: 'timeout',
      title: 'Сервис не ответил вовремя',
      description: 'Запрос отменён через 30 секунд. Повторите действие.',
      retryable: true,
    })
  })
})
```

Create `apps/web/src/shared/auth/protectedBrowserStorage.test.ts`. Seed `localStorage` with multiple numeric-user keys under `robopark.recentRobots.v2.` and multiple versioned/user keys under `robopark:report-draft:`, plus `robopark-theme`, `robopark-density`, the legacy unscoped `robopark.recentRobots`, and an unrelated key. Assert one `clearProtectedBrowserStorage(localStorage)` removes every protected-prefix key across all users/versions and preserves every unrelated key. Also stub a throwing/quota-limited `Storage` and prove cleanup never breaks auth fail-close.

Create `apps/web/src/auth.test.tsx` with a seeded protected `resourceStore` entry and both protected local-storage prefixes. Prove that an initial authenticated render followed by `refreshUser()` returning `ApiError(401)` clears the cache, all users' protected storage, and current user before rejecting to the caller. Separately make `api.logout()` reject with `TypeError` and prove the same cleanup still runs in `finally`.

Add the numeric-ID reuse regression: seed report draft and recent robots for an old synthetic account `id=3`, end that session, then make the next login return a newly created account with the same `id=3`. Assert the protected keys are already absent when `api.login` begins and the replacement account cannot restore either stale payload. This test must not rely on comparing the two user IDs. Add a control where an authenticated `refreshUser()` fails with offline `TypeError`/timeout or succeeds during an ordinary reload; assert user and protected storage remain intact because those events are not session boundaries.

- [ ] **Step 2: Run the API/error tests and confirm RED**

```bash
cd apps/web
npx vitest run \
  src/api.test.ts \
  src/shared/api/classifyApiError.test.ts \
  src/shared/auth/protectedBrowserStorage.test.ts \
  src/auth.test.tsx
```

Expected: FAIL because `ApiError` has no `requestId`, `ApiTimeoutError`, the classifier/storage modules do not exist, stalled fetches are not cancelled, and login/refresh/logout are not fail-closed.

- [ ] **Step 3: Carry request IDs and enforce transport deadlines**

Replace the `ApiError` class and each non-OK throw in `request`, `requestBlob`, and `requestForm`:

```typescript
export class ApiError extends Error {
  status: number
  detail: string | null
  requestId?: string

  constructor(status: number, detail: string | null = null, requestId?: string) {
    super(detail ?? String(status))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.requestId = requestId
  }
}

export class ApiTimeoutError extends Error {
  constructor(public readonly timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`)
    this.name = 'ApiTimeoutError'
  }
}

const JSON_TIMEOUT_MS = 30_000
const BLOB_TIMEOUT_MS = 60_000
const FORM_TIMEOUT_MS = 90_000

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController()
  const sourceSignal = init.signal
  let timedOut = false
  const forwardAbort = () => controller.abort(sourceSignal?.reason)
  if (sourceSignal?.aborted) forwardAbort()
  else sourceSignal?.addEventListener('abort', forwardAbort, { once: true })
  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    return await fetch(input, { ...init, signal: controller.signal })
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(timeoutMs)
    throw error
  } finally {
    window.clearTimeout(timer)
    sourceSignal?.removeEventListener('abort', forwardAbort)
  }
}

function responseRequestId(response: Response): string | undefined {
  return response.headers.get('X-Request-ID')?.trim() || undefined
}

// Use this exact throw in request, requestBlob and requestForm.
throw new ApiError(response.status, detail, responseRequestId(response))
```

Replace their direct `fetch` calls with `fetchWithTimeout`: `request` uses `JSON_TIMEOUT_MS`, `requestBlob` uses `BLOB_TIMEOUT_MS`, and `requestForm` uses `FORM_TIMEOUT_MS`. Preserve each helper's existing headers, credentials, method, and body. A caller-provided abort remains an ordinary cancellation; only the helper's own deadline becomes `ApiTimeoutError`.

Create `classifyApiError.ts`:

```typescript
import { ApiError, ApiTimeoutError } from '../../api'
import { mapApiError } from '../../i18n/errors'

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

const titles: Record<Exclude<DomainErrorKind, 'offline' | 'timeout'>, string> = {
  unauthorized: 'Сессия истекла',
  forbidden: 'Нет доступа',
  'not-found': 'Не найдено',
  conflict: 'Данные изменились',
  configuration: 'Требуется настройка',
  server: 'Сервис временно недоступен',
  unknown: 'Не удалось выполнить действие',
}

const retryable: Record<Exclude<DomainErrorKind, 'offline' | 'timeout'>, boolean> = {
  unauthorized: false,
  forbidden: false,
  'not-found': false,
  conflict: true,
  configuration: false,
  server: true,
  unknown: false,
}

const configurationDetails = new Set([
  'tracker_token_not_configured',
  'emergency_cookie_not_configured',
  'emergency_cookie_invalid',
])

export function classifyApiError(error: unknown, fallback: string): DomainError {
  if (error instanceof ApiTimeoutError) {
    return {
      kind: 'timeout',
      title: 'Сервис не ответил вовремя',
      description: `Запрос отменён через ${Math.round(error.timeoutMs / 1000)} секунд. Повторите действие.`,
      retryable: true,
    }
  }
  if (error instanceof TypeError || (typeof navigator !== 'undefined' && !navigator.onLine)) {
    return {
      kind: 'offline',
      title: 'Нет сети',
      description: 'Проверьте подключение и повторите действие.',
      retryable: true,
    }
  }

  const kind: Exclude<DomainErrorKind, 'offline' | 'timeout'> = !(error instanceof ApiError)
    ? 'unknown'
    : error.status === 401
      ? 'unauthorized'
      : configurationDetails.has(error.detail ?? '')
        ? 'configuration'
        : error.status === 403
          ? 'forbidden'
          : error.status === 404
            ? 'not-found'
            : error.status === 409
              ? 'conflict'
              : error.status >= 500
                ? 'server'
                : 'unknown'
  const description = mapApiError(error, fallback) || fallback
  const requestId = error instanceof ApiError ? error.requestId : undefined
  return {
    kind,
    title: titles[kind],
    description,
    retryable: retryable[kind],
    ...(requestId ? { requestId } : {}),
  }
}
```

Map `emergency_cookie_invalid` in `i18n/errors.ts`/`ru.ts` to the product-safe description `Интеграция проверки робота требует внимания.`; remove the old visible “Cookie Emergency” text. Because configuration matching runs before the generic `403` branch, resolver, legacy redirect, Work panel and robot workspace must never mislabel this detail as a user-scope denial. UI actions remain role-aware: admin/royal (or a custom user with the relevant administration permission) gets `Открыть настройки`; everyone else gets `Обратиться к администратору` with no inaccessible link.

Create `shared/auth/protectedBrowserStorage.ts` as the only owner of protected persistent prefixes:

```ts
export const RECENT_ROBOTS_V2_STORAGE_PREFIX = 'robopark.recentRobots.v2.'
export const REPORT_DRAFT_STORAGE_PREFIX = 'robopark:report-draft:'

const PROTECTED_BROWSER_STORAGE_PREFIXES = [
  RECENT_ROBOTS_V2_STORAGE_PREFIX,
  REPORT_DRAFT_STORAGE_PREFIX,
] as const

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function clearProtectedBrowserStorage(storage?: Storage): void {
  const target = resolveStorage(storage)
  if (!target) return
  try {
    const keys = Array.from({ length: target.length }, (_, index) => target.key(index))
      .filter((key): key is string => key !== null)
      .filter((key) => PROTECTED_BROWSER_STORAGE_PREFIXES.some(
        (prefix) => key.startsWith(prefix),
      ))
    for (const key of keys) target.removeItem(key)
  } catch {
    // Unavailable browser storage must not block local auth fail-close.
  }
}
```

Snapshot keys before removal so index compaction cannot skip a namespace. This contract intentionally clears **all** user suffixes: a deleted account may later be recreated with the same numeric database ID, so per-current-user cleanup is not a security boundary.

In `auth.tsx`, extract one local `clearSessionState()` that calls `resourceStore.clearAll()`, `clearProtectedBrowserStorage()`, and clears `user`. Call it immediately before `api.login`, not after a successful response. `refreshUser` keeps the current success behavior, but catches `ApiError(401)`, calls that helper, then rethrows; other errors rethrow without inventing logout or clearing recoverable browser state. `logout` awaits the server request inside `try` and always calls `clearSessionState()` in `finally`, so an expired/offline logout cannot leave protected cached or persisted data for the next account. Do not call the helper on successful boot refresh, ordinary reload, browser offline, timeout, or retryable 5xx: same-session drafts and recents must survive those transient events.

- [ ] **Step 4: Write a RED connectivity-hook test**

Create `apps/web/src/shared/browser/useOnlineStatus.test.tsx`:

```tsx
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useOnlineStatus } from './useOnlineStatus'

function Probe() {
  return <output>{useOnlineStatus() ? 'online' : 'offline'}</output>
}

describe('useOnlineStatus', () => {
  it('tracks browser online and offline events', () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    render(<Probe />)
    expect(screen.getByText('online')).toBeInTheDocument()

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })
    act(() => window.dispatchEvent(new Event('offline')))
    expect(screen.getByText('offline')).toBeInTheDocument()

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    act(() => window.dispatchEvent(new Event('online')))
    expect(screen.getByText('online')).toBeInTheDocument()
  })
})
```

- [ ] **Step 5: Run the hook test and confirm RED**

```bash
cd apps/web
npx vitest run src/shared/browser/useOnlineStatus.test.tsx
```

Expected: FAIL because `useOnlineStatus.ts` does not exist.

- [ ] **Step 6: Implement the connectivity hook**

Create `apps/web/src/shared/browser/useOnlineStatus.ts`:

```typescript
import { useSyncExternalStore } from 'react'

function subscribe(onStoreChange: () => void): () => void {
  window.addEventListener('online', onStoreChange)
  window.addEventListener('offline', onStoreChange)
  return () => {
    window.removeEventListener('online', onStoreChange)
    window.removeEventListener('offline', onStoreChange)
  }
}

function getSnapshot(): boolean {
  return navigator.onLine
}

export function useOnlineStatus(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => true)
}
```

- [ ] **Step 7: Run GREEN and commit**

```bash
cd apps/web
npx vitest run \
  src/api.test.ts \
  src/auth.test.tsx \
  src/shared/auth/protectedBrowserStorage.test.ts \
  src/shared/api/classifyApiError.test.ts \
  src/shared/browser/useOnlineStatus.test.tsx \
  src/i18n/errors.test.ts
npm run build
git diff --check
git diff --name-only
git add \
  src/api.ts \
  src/api.test.ts \
  src/auth.tsx \
  src/auth.test.tsx \
  src/shared/auth/protectedBrowserStorage.ts \
  src/shared/auth/protectedBrowserStorage.test.ts \
  src/shared/api/classifyApiError.ts \
  src/shared/api/classifyApiError.test.ts \
  src/shared/browser/useOnlineStatus.ts \
  src/shared/browser/useOnlineStatus.test.tsx \
  src/i18n/errors.ts \
  src/i18n/errors.test.ts \
  src/i18n/ru.ts
git commit -m "feat(web): classify operational failures and connectivity"
```

Expected: all listed tests and the TypeScript build pass.

---

### Task 2: Add truthful operational API metadata and capability contracts

**Files:**

- Modify: `apps/api/src/robopark_api/schemas.py:188-207,302-325,411-417`
- Modify: `apps/api/src/robopark_api/routers/emergency.py:123-135`
- Modify: `apps/api/src/robopark_api/routers/dashboard.py:46-101`
- Modify: `apps/api/src/robopark_api/deps.py:154-168`
- Modify: `apps/api/src/robopark_api/routers/tracker_read.py:1-293`
- Modify: `apps/api/src/robopark_api/services/tracker_policy.py:66-76`
- Modify: `apps/api/src/robopark_api/services/rbac.py:105-162`
- Modify: `apps/api/src/robopark_api/services/rbac_seed.py:15-21`
- Modify: `apps/api/tests/test_emergency_router.py:219-230`
- Modify: `apps/api/tests/test_dashboard_router.py:42-82`
- Modify: `apps/api/tests/test_tracker_read.py`
- Modify: `apps/api/tests/test_tracker_actions.py`
- Modify: `apps/api/tests/test_access_requests.py:118-133`
- Modify: `apps/api/tests/test_rbac_seed.py`
- Create: `apps/api/tests/test_custom_role_operational_scope.py`
- Modify: `apps/web/src/api.ts:174-194,230-276,307-314,688-705`
- Modify: `apps/web/src/api.test.ts`
- Modify: `apps/web/src/components/emergency/robotHud.test.ts`
- Modify: `apps/web/src/components/tracker/robotHealth.test.ts`

**Interfaces:**

- Consumes: current protected `GET /dashboard/summary`, `GET /tracker/issues`, `GET /tracker/issues/:key`, `GET /emergency/:vin/snapshot` and existing RBAC scope enforcement.
- Produces: `DashboardSummary.generated_at: string`; `EmergencySnapshot.observed_at: string`; `TrackerIssueCapabilities = { comment; assign; unassign; transition; close; attach }`; `TrackerIssueDetail.capabilities: TrackerIssueCapabilities`; `api.trackerIssues(params & { sort?: 'oldest' | 'newest' })` with deterministic default `sort=oldest`.
- Enforcement invariant: every `true` capability maps to the same backend rule used by its mutation. `attach` requires `tracker.attach`; the other five require `can_write_tracker(...)`.
- Produces: default approved driver permissions include `nav.dashboard`, `nav.robot_search`, `nav.emergency`; technical keys remain unchanged.
- Produces: custom roles remain raw slugs and receive the same fail-closed assigned-park scope as operators when effective permissions grant dashboard/Tracker/robot access; a navigation key alone never grants a backend mutation.
- Semantics: `generated_at` is time the dashboard aggregation completed; `observed_at` is server observation time of the returned payload, not an upstream telemetry event timestamp.

- [ ] **Step 1: Write backend RED contract tests**

Extend the existing dashboard and Emergency happy-path assertions:

```python
# apps/api/tests/test_dashboard_router.py, inside test_dashboard_summary_operator_ok
generated_at = datetime.fromisoformat(body["generated_at"])
assert generated_at.tzinfo is not None

# apps/api/tests/test_emergency_router.py, inside test_snapshot_returns_hud_when_allowed
from datetime import datetime

observed_at = datetime.fromisoformat(body["observed_at"])
assert observed_at.tzinfo is not None
```

Add these tests to `apps/api/tests/test_tracker_read.py`:

```python
def _scoped_issue(key: str, created: str) -> dict:
    return {
        "key": key,
        "summary": f"blocker [{key[-1]}]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "created": created,
        "hours_created": "1",
        "tags": ["Alpha"],
        "robot": key[-1],
    }


def test_tracker_list_honors_oldest_and_newest_sort(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [
            _scoped_issue("ROBOPARK-2", "2026-01-02T00:00:00Z"),
            _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
        ],
    )
    login_as(client, "op2", "secret")

    oldest = client.get("/tracker/issues?sort=oldest").json()["items"]
    newest = client.get("/tracker/issues?sort=newest").json()["items"]

    assert [item["key"] for item in oldest] == ["ROBOPARK-1", "ROBOPARK-2"]
    assert [item["key"] for item in newest] == ["ROBOPARK-2", "ROBOPARK-1"]


def test_mechanic_issue_capabilities_respect_write_policy_but_keep_attachment(
    client, db_session, seed_mechanic, monkeypatch
):
    from robopark_api.services import tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_MECHANIC_WRITE_KEY,
        False,
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: _scoped_issue("ROBOPARK-1", "2026-01-01T00:00:00Z"),
    )
    login_as(client, "mech1", "secret")

    response = client.get("/tracker/issues/ROBOPARK-1")

    assert response.status_code == 200
    assert response.json()["capabilities"] == {
        "comment": False,
        "assign": False,
        "unassign": False,
        "transition": False,
        "close": False,
        "attach": True,
    }
```

Add this authorization regression to `apps/api/tests/test_tracker_actions.py`:

```python
def test_attachment_is_denied_without_tracker_attach_permission(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import rbac, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    mechanic = rbac.load_user_with_role(db_session, seed_mechanic.id)
    assert mechanic is not None
    desired = sorted(
        rbac.role_permission_keys(db_session, mechanic) - {rbac.PERMISSION_TRACKER_ATTACH}
    )
    rbac.set_user_effective_permissions(db_session, mechanic, desired)
    db_session.commit()
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-1",
            "summary": "blocker [447]",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "resolution": "",
            "tags": ["Alpha"],
        },
    )
    login_as(client, "mech1", "secret")

    response = client.post(
        "/tracker/issues/ROBOPARK-1/attachments",
        files={"file": ("robot.jpg", b"image", "image/jpeg")},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_attach_disabled"
```

Change the driver test to assert the approved product routes while retaining Emergency API permission:

```python
# apps/api/tests/test_access_requests.py
def test_driver_registration_gets_overview_robot_and_check_permissions(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    user_id = register_user(client, test_settings, monkeypatch, "driver1", role_slug="driver")
    login_as(client, "royal", "secret")
    assert client.post(f"/admin/users/{user_id}/approve", json={"park_ids": []}).status_code == 204

    client.post("/auth/logout")
    login_as(client, "driver1", VALID_PASSWORD)
    permissions = set(client.get("/auth/me").json()["permissions"])

    assert {"nav.dashboard", "nav.robot_search", "nav.emergency"} <= permissions
    assert "tracker.read" not in permissions
```

Add to `test_rbac_seed.py`:

```python
def test_ensure_rbac_catalog_adds_driver_overview_and_robot_search(db_session):
    driver = db_session.scalar(select(Role).where(Role.slug == "driver"))
    assert driver is not None
    removed_ids = list(
        db_session.scalars(
            select(Permission.id).where(
                Permission.key.in_(["nav.dashboard", "nav.robot_search"])
            )
        )
    )
    db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == driver.id,
            RolePermission.permission_id.in_(removed_ids),
        )
    )
    db_session.commit()

    ensure_rbac_catalog(db_session)

    keys = set(
        db_session.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == driver.id)
        )
    )
    assert {"nav.dashboard", "nav.robot_search", "nav.emergency"} <= keys
```

This verifies an existing installation is upgraded, not only a newly seeded test database.

Create `test_custom_role_operational_scope.py`. Seed a non-system `Role(slug="field_lead", ...)`, attach the existing catalog permissions `nav.dashboard`, `nav.tasks`, `nav.robot_search`, `nav.emergency`, `tracker.read`, and `tracker.write`, create an approved synthetic user assigned to park `Alpha`, and log in through `login_as`. Select `Permission` rows from the seeded catalog and assign `role.permissions`; do not insert duplicate permission keys. With deterministic Tracker/dashboard monkeypatches, assert:

```python
def test_custom_operational_role_uses_only_its_assigned_park(...):
    own_summary = client.get(f"/dashboard/summary?park_id={alpha.id}")
    foreign_summary = client.get(f"/dashboard/summary?park_id={beta.id}")
    issues = client.get("/tracker/issues?queue=ROBOPARK&park=Alpha")

    assert own_summary.status_code == 200
    assert foreign_summary.status_code == 403
    assert issues.status_code == 200
    assert [item["key"] for item in issues.json()["items"]] == ["ROBOPARK-42"]


def test_custom_role_without_backend_capability_is_denied(...):
    set_effective_permissions(field_lead, ["nav.tasks"])
    assert client.get(f"/dashboard/summary?park_id={alpha.id}").status_code == 403
    assert client.get("/tracker/issues").status_code == 403
```

The Tracker fixture returns one Alpha-tagged issue and one Beta-tagged issue so the first test proves filtering, not merely authentication. Add one direct `vin_allowed_for_user(db_session, field_lead, vin)` assertion with a mocked matching Tracker ticket, proving `/emergency` shares the same assigned-park policy. No custom role is treated as admin/royal or allowed an unscoped query.

- [ ] **Step 2: Run focused backend tests and confirm RED**

```bash
cd apps/api
uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q \
  tests/test_dashboard_router.py::test_dashboard_summary_operator_ok \
  tests/test_emergency_router.py::test_snapshot_returns_hud_when_allowed \
  tests/test_tracker_read.py::test_tracker_list_honors_oldest_and_newest_sort \
  tests/test_tracker_read.py::test_mechanic_issue_capabilities_respect_write_policy_but_keep_attachment \
  tests/test_tracker_actions.py::test_attachment_is_denied_without_tracker_attach_permission \
  tests/test_rbac_seed.py::test_ensure_rbac_catalog_adds_driver_overview_and_robot_search \
  tests/test_access_requests.py::test_driver_registration_gets_overview_robot_and_check_permissions \
  tests/test_custom_role_operational_scope.py
```

Expected: FAIL because timestamps and `capabilities` are absent, `sort` is ignored, the driver lacks overview/robot-search permissions, and custom roles are rejected by the current slug allowlists.

- [ ] **Step 3: Add the minimal additive backend implementation**

Add schema fields and the capability object in `schemas.py`:

```python
class EmergencySnapshotOut(BaseModel):
    vin: str
    short_number: str
    observed_at: datetime
    online: bool | None = None
    speed: float | None = None
    charge_percent: float | None = None
    battery1_percent: float | None = None
    battery2_percent: float | None = None
    disk_percent: float | None = None
    mode: str | None = None
    icp_label: str | None = None
    icp_ok: bool | None = None
    lte_label: str | None = None
    lte_ok: bool | None = None
    connection: Literal["lte", "wire"] | None = None
    error_banner: str | None = None
    lat: float | None = None
    lon: float | None = None
    heading_deg: float | None = None
    wheels_fault: list[str] = []


class TrackerIssueCapabilitiesOut(BaseModel):
    comment: bool
    assign: bool
    unassign: bool
    transition: bool
    close: bool
    attach: bool


class TrackerIssueDetailOut(TrackerIssueOut):
    resolution: str | None = None
    description: str | None = None
    reporter: TrackerPersonOut | None = None
    components: list[str] = Field(default_factory=list)
    attachments: list[TrackerAttachmentOut] = Field(default_factory=list)
    capabilities: TrackerIssueCapabilitiesOut


class DashboardSummaryOut(BaseModel):
    park_id: int
    generated_at: datetime
    arrived: int
    done: int
    queued: int
    in_transit: int
    moving: list[DashboardMovingItemOut]
```

Stamp the two responses at the server boundary:

```python
# apps/api/src/robopark_api/routers/emergency.py
from datetime import UTC, datetime

snap = parse_emergency_snapshot(payload, vin=vin)
return EmergencySnapshotOut(**snap, observed_at=datetime.now(UTC))

# apps/api/src/robopark_api/routers/dashboard.py
from datetime import UTC, datetime

# Include this keyword in both DashboardSummaryOut return sites.
generated_at=datetime.now(UTC),
```

Extend Tracker detail and sorting in `tracker_read.py`:

```python
from typing import Literal

from robopark_api.schemas import TrackerIssueCapabilitiesOut
from robopark_api.services.tracker_policy import can_write_tracker


def _detail_out(issue: dict, *, db: Session, user: User) -> TrackerIssueDetailOut:
    attachments = [TrackerAttachmentOut(**item) for item in (issue.get("attachments") or [])]
    writable = can_write_tracker(db, user)
    capabilities = TrackerIssueCapabilitiesOut(
        comment=writable,
        assign=writable,
        unassign=writable,
        transition=writable,
        close=writable,
        attach=rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH),
    )
    return TrackerIssueDetailOut(
        **_issue_out(issue).model_dump(),
        resolution=str(issue.get("resolution") or ""),
        description=str(issue.get("description") or ""),
        reporter=_person_out(issue.get("reporter")),
        components=[str(item) for item in (issue.get("components") or [])],
        attachments=attachments,
        capabilities=capabilities,
    )


def list_issues(
    queue: str | None = Query(default=None),
    park: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    robot: str | None = Query(default=None),
    assignee: str | None = Query(default=None, max_length=128),
    untagged: bool = Query(default=False),
    age_hours: int | None = Query(default=None, ge=1),
    sort_order: Literal["oldest", "newest"] = Query(default="oldest", alias="sort"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssuesOut:
    _ensure_tracker_user(user, db)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )
    query_text = _build_query(
        user=user,
        db=db,
        queue=queue,
        park=park,
        status_filter=status_filter,
        robot=robot,
        assignee=assignee,
        untagged=untagged,
    )
    try:
        items = tracker_cache.search_issues(token=token, query=query_text)
    except tracker_client.TrackerError as exc:
        logger.exception("tracker search failed query=%r", query_text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    ordered = tracker_filters.sort_issues_oldest_first(items)
    if sort_order == "newest":
        ordered.reverse()
    scoped: list[TrackerIssueOut] = []
    for issue in ordered:
        if not is_issue_in_scope(db, user, issue):
            continue
        if age_hours and issue.get("hours_created"):
            try:
                if float(issue["hours_created"]) < age_hours:
                    continue
            except (TypeError, ValueError):
                pass
        scoped.append(_issue_out(issue))
    total = len(scoped)
    page = scoped[offset : offset + limit]
    return TrackerIssuesOut(
        items=page,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(page) < total,
    )
```

Change the existing detail return to:

```python
enforce_issue_scope(db, user, issue)
return _detail_out(issue, db=db, user=user)
```

Make attachment authorization use the same permission as the advertised capability in `tracker_policy.py`:

```python
def ensure_action_allowed(db: Session, user: User, issue: dict, action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    enforce_issue_scope(db, user, issue)
    if action == "attach":
        if not rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_attach_disabled",
            )
        return
    if not can_write_tracker(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_write_disabled",
        )
```

Update system-role copy and default driver permissions without renaming keys:

```python
# rbac.py
PermissionDef(PERMISSION_NAV_EMERGENCY, "nav", "Проверка робота", 40),

RoleSlug.DRIVER: frozenset(
    {
        PERMISSION_NAV_DASHBOARD,
        PERMISSION_NAV_ROBOT_SEARCH,
        PERMISSION_NAV_EMERGENCY,
    }
),

# rbac_seed.py
RoleSlug.DRIVER: (
    "Водитель",
    "Обзор, поиск и проверка робота без доступа к задачам Tracker",
),
```

Replace the system-slug branch in `require_dashboard_park` with effective permission plus fail-closed scope:

```python
def require_dashboard_park(park_id: int, db: Session, user: User) -> Park:
    park = db.get(Park, park_id)
    if park is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    rbac.require_approved_permission(db, user, rbac.PERMISSION_NAV_DASHBOARD)
    if rbac.is_admin_or_royal(user) or rbac.has_permission(
        db, user, rbac.PERMISSION_PARKS_MANAGE
    ):
        return park
    return require_operator_park(park_id, db, user)
```

In `tracker_policy.py`, remove the `OPERATOR/MECHANIC` slug allowlists from `allowed_queues_for_user`, `allowed_park_tags_for_user`, and `_check_issue_scope`. Admin/royal retain their existing unscoped branches. Every other role receives queues/tags only from `get_user_parks(db, user)`, and only when `tracker.read` or `tracker.write` is effective; `_check_issue_scope` then keeps the existing mandatory queue plus assigned-tag proof. Keep the mechanic-only write toggle and operator-only untagged setting role-specific. This makes custom operational roles capability-driven without widening data scope, and it also makes `emergency_scope.vin_allowed_for_user` reuse the same proof.

- [ ] **Step 4: Add frontend DTOs and a RED transport test**

Append this second `it` block inside the existing `describe('API transport metadata', ...)` in `apps/web/src/api.test.ts`:

```typescript
it('sends the deterministic oldest sort when work filters omit it', async () => {
  const fetchMock = vi.fn(async () =>
    new Response(
      JSON.stringify({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ),
  )
  vi.stubGlobal('fetch', fetchMock)

  await api.trackerIssues({ limit: 50, offset: 0 })

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/tracker/issues?sort=oldest&limit=50&offset=0',
    expect.objectContaining({ credentials: 'include' }),
  )
})
```

Run:

```bash
cd apps/web
npx vitest run src/api.test.ts
```

Expected: FAIL because the current transport does not add `sort=oldest`.

- [ ] **Step 5: Implement frontend types and deterministic query serialization**

Add these exact fields/types to `api.ts`:

```typescript
export type EmergencySnapshot = {
  vin: string
  short_number: string
  observed_at: string
  online: boolean | null
  speed: number | null
  charge_percent: number | null
  battery1_percent: number | null
  battery2_percent: number | null
  disk_percent: number | null
  mode: string | null
  icp_label: string | null
  icp_ok: boolean | null
  lte_label: string | null
  lte_ok: boolean | null
  connection: 'lte' | 'wire' | null
  error_banner: string | null
  lat: number | null
  lon: number | null
  heading_deg: number | null
  wheels_fault: string[]
}

export type TrackerIssueCapabilities = {
  comment: boolean
  assign: boolean
  unassign: boolean
  transition: boolean
  close: boolean
  attach: boolean
}

export type TrackerIssueDetail = TrackerIssue & {
  resolution?: string | null
  description?: string | null
  reporter?: TrackerPerson | null
  components?: string[]
  attachments?: TrackerAttachment[]
  capabilities: TrackerIssueCapabilities
}

export type DashboardSummary = {
  park_id: number
  generated_at: string
  arrived: number
  done: number
  queued: number
  in_transit: number
  moving: DashboardMovingItem[]
}
```

Make `sort` explicit and first in query order:

```typescript
trackerIssues: (params: {
  queue?: string
  park?: string
  status?: string
  robot?: string
  assignee?: string
  untagged?: boolean
  age_hours?: number
  sort?: 'oldest' | 'newest'
  limit?: number
  offset?: number
}) => {
  const q = new URLSearchParams({ sort: params.sort ?? 'oldest' })
  Object.entries(params).forEach(([key, value]) => {
    if (key === 'sort') return
    if (value !== undefined && value !== null && value !== '') {
      q.set(key, String(value))
    }
  })
  return request<Paged<TrackerIssue>>(`/tracker/issues?${q.toString()}`)
},
```

Because `observed_at` is required, add this property to the base snapshot returned by `snapshot(...)` in both `robotHud.test.ts` and `robotHealth.test.ts`:

```typescript
observed_at: '2026-09-02T09:00:00Z',
```

- [ ] **Step 6: Run GREEN and API regressions**

```bash
cd apps/api
uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q \
  tests/test_dashboard_router.py \
  tests/test_emergency_router.py \
  tests/test_emergency_snapshot.py \
  tests/test_tracker_read.py \
  tests/test_tracker_issue_fields.py \
  tests/test_tracker_scope.py \
  tests/test_tracker_actions.py \
  tests/test_access_requests.py \
  tests/test_rbac_seed.py \
  tests/test_driver_emergency_scope.py \
  tests/test_custom_role_operational_scope.py
cd ../web
npx vitest run src/api.test.ts
npm run build
git diff --check
git diff --name-only
```

Expected: all selected API tests pass, frontend test passes, and TypeScript build succeeds.

- [ ] **Step 7: Commit**

```bash
git add \
  apps/api/src/robopark_api/schemas.py \
  apps/api/src/robopark_api/routers/emergency.py \
  apps/api/src/robopark_api/routers/dashboard.py \
  apps/api/src/robopark_api/deps.py \
  apps/api/src/robopark_api/routers/tracker_read.py \
  apps/api/src/robopark_api/services/tracker_policy.py \
  apps/api/src/robopark_api/services/rbac.py \
  apps/api/src/robopark_api/services/rbac_seed.py \
  apps/api/tests/test_emergency_router.py \
  apps/api/tests/test_dashboard_router.py \
  apps/api/tests/test_tracker_read.py \
  apps/api/tests/test_tracker_actions.py \
  apps/api/tests/test_access_requests.py \
  apps/api/tests/test_rbac_seed.py \
  apps/api/tests/test_custom_role_operational_scope.py \
  apps/web/src/api.ts \
  apps/web/src/api.test.ts \
  apps/web/src/components/emergency/robotHud.test.ts \
  apps/web/src/components/tracker/robotHealth.test.ts
git commit -m "feat(operations): expose freshness and action capabilities"
```

---

### Task 3: Make `/work` state canonical, typed and restorable

**Files:**

- Create: `apps/web/src/domains/work/workUrl.ts`
- Create: `apps/web/src/domains/work/workUrl.test.ts`
- Create: `apps/web/src/domains/work/workData.ts`
- Create: `apps/web/src/domains/work/workData.test.ts`
- Consume unchanged: `apps/web/src/app/park/ParkScopeProvider.tsx`

**Interfaces:**

- Consumes: `api.trackerIssues` from Task 2, `Park.tracker_queue`/`Park.tag`, and Foundation `PARK_QUERY_KEY = 'park'`/`useParkScope()`.
- Produces: `WorkSort = 'oldest' | 'newest'`; `WorkFilters`; `WorkUrlState`; `WorkDefaults`.
- Produces: `parseWorkUrl(params, defaults): WorkUrlState`; `buildWorkSearch(state, parkId): string`; `workListHref(state, parkId): string`; `workIssueHref(issueKey, state, parkId): string`.
- Produces: `WorkApiClient = Pick<typeof api, 'trackerIssues'>`; `loadWorkPage(client, state, parkTag): Promise<Paged<TrackerIssue>>`; `WORK_PAGE_SIZE = 50`.
- Produces: `saveWorkScroll(userId, search, scrollTop): void`; `readWorkScroll(userId, search): number`; scroll is scoped to the authenticated numeric user ID and current canonical query string.
- Ownership rule: URL key `park` always contains the numeric Foundation park ID. The Tracker tag is derived from `selectedPark.tag`; the Workbench never writes a second park value into that key.

- [ ] **Step 1: Write RED URL-codec tests**

Create `apps/web/src/domains/work/workUrl.test.ts`:

```typescript
import { afterEach, describe, expect, it } from 'vitest'
import {
  buildWorkSearch,
  parseWorkUrl,
  readWorkScroll,
  saveWorkScroll,
  workIssueHref,
  workListHref,
} from './workUrl'

const defaults = { queue: 'ROBOPARK' }

describe('work URL state', () => {
  afterEach(() => sessionStorage.clear())

  it('parses filters, sort and a positive page', () => {
    const state = parseWorkUrl(
      new URLSearchParams(
        'park=7&queue=OPS&status=open&robot=447&assignee=ivan&untagged=1&age=24&sort=newest&page=3',
      ),
      defaults,
    )
    expect(state).toEqual({
      filters: {
        queue: 'OPS',
        status: 'open',
        robot: '447',
        assignee: 'ivan',
        untagged: true,
        ageHours: 24,
      },
      sort: 'newest',
      page: 3,
    })
  })

  it('normalizes invalid values and leaves the Foundation park key untouched', () => {
    expect(parseWorkUrl(new URLSearchParams('page=-2&age=0&sort=random'), defaults)).toEqual({
      filters: { queue: 'ROBOPARK' },
      sort: 'oldest',
      page: 1,
    })
  })

  it('serializes stable links for list and detail', () => {
    const state = parseWorkUrl(new URLSearchParams('status=open&sort=newest&page=2'), defaults)
    expect(buildWorkSearch(state, 7)).toBe(
      '?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
    expect(workListHref(state, 7)).toBe(
      '/work?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
    expect(workIssueHref('ROBOPARK-42', state, 7)).toBe(
      '/work/ROBOPARK-42?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
  })

  it('keeps scroll positions isolated by user and query', () => {
    saveWorkScroll(7, '?status=open', 480)
    expect(readWorkScroll(7, '?status=open')).toBe(480)
    expect(readWorkScroll(8, '?status=open')).toBe(0)
    expect(readWorkScroll(7, '?status=closed')).toBe(0)
  })
})
```

- [ ] **Step 2: Run the URL tests and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/work/workUrl.test.ts
```

Expected: FAIL because `workUrl.ts` does not exist.

- [ ] **Step 3: Implement the URL codec and scroll key**

Create `apps/web/src/domains/work/workUrl.ts`:

```typescript
export type WorkSort = 'oldest' | 'newest'

export type WorkFilters = {
  queue?: string
  status?: string
  robot?: string
  assignee?: string
  untagged?: boolean
  ageHours?: number
}

export type WorkUrlState = {
  filters: WorkFilters
  sort: WorkSort
  page: number
}

export type WorkDefaults = Pick<WorkFilters, 'queue'>

function text(params: URLSearchParams, key: string): string | undefined {
  return params.get(key)?.trim() || undefined
}

function positiveInteger(raw: string | null): number | undefined {
  if (!raw || !/^\d+$/.test(raw)) return undefined
  const value = Number(raw)
  return Number.isSafeInteger(value) && value > 0 ? value : undefined
}

export function parseWorkUrl(params: URLSearchParams, defaults: WorkDefaults): WorkUrlState {
  const untagged = params.get('untagged') === '1'
  const queue = text(params, 'queue') ?? defaults.queue
  const ageHours = positiveInteger(params.get('age'))
  const sort: WorkSort = params.get('sort') === 'newest' ? 'newest' : 'oldest'
  return {
    filters: {
      ...(queue ? { queue } : {}),
      ...(text(params, 'status') ? { status: text(params, 'status') } : {}),
      ...(text(params, 'robot') ? { robot: text(params, 'robot') } : {}),
      ...(text(params, 'assignee') ? { assignee: text(params, 'assignee') } : {}),
      ...(untagged ? { untagged: true } : {}),
      ...(ageHours ? { ageHours } : {}),
    },
    sort,
    page: positiveInteger(params.get('page')) ?? 1,
  }
}

export function buildWorkSearch(state: WorkUrlState, parkId: number | null): string {
  const params = new URLSearchParams()
  const { filters } = state
  if (parkId != null) params.set('park', String(parkId))
  if (filters.queue) params.set('queue', filters.queue)
  if (filters.status) params.set('status', filters.status)
  if (filters.robot) params.set('robot', filters.robot)
  if (filters.assignee) params.set('assignee', filters.assignee)
  if (filters.untagged) params.set('untagged', '1')
  if (filters.ageHours) params.set('age', String(filters.ageHours))
  if (state.sort !== 'oldest') params.set('sort', state.sort)
  if (state.page > 1) params.set('page', String(state.page))
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function workListHref(state: WorkUrlState, parkId: number | null): string {
  return `/work${buildWorkSearch(state, parkId)}`
}

export function workIssueHref(
  issueKey: string,
  state: WorkUrlState,
  parkId: number | null,
): string {
  return `/work/${encodeURIComponent(issueKey)}${buildWorkSearch(state, parkId)}`
}

function scrollKey(userId: number, search: string): string {
  return `robopark.work.scroll.${userId}.${encodeURIComponent(search)}`
}

export function saveWorkScroll(userId: number, search: string, scrollTop: number): void {
  sessionStorage.setItem(scrollKey(userId, search), String(Math.max(0, Math.round(scrollTop))))
}

export function readWorkScroll(userId: number, search: string): number {
  const value = Number(sessionStorage.getItem(scrollKey(userId, search)))
  return Number.isFinite(value) && value > 0 ? value : 0
}
```

- [ ] **Step 4: Write a RED request-mapping test**

Create `apps/web/src/domains/work/workData.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import type { Paged, TrackerIssue } from '../../api'
import { loadWorkPage, WORK_PAGE_SIZE, type WorkApiClient } from './workData'

describe('loadWorkPage', () => {
  it('maps canonical URL state to Tracker pagination', async () => {
    const result: Paged<TrackerIssue> = {
      items: [],
      total: 0,
      limit: WORK_PAGE_SIZE,
      offset: WORK_PAGE_SIZE * 2,
      has_more: false,
    }
    const trackerIssues = vi.fn(async () => result)
    const client = { trackerIssues } as WorkApiClient

    await loadWorkPage(client, {
      filters: {
        queue: 'ROBOPARK',
        status: 'open',
        robot: '447',
        assignee: 'ivan',
        ageHours: 24,
      },
      sort: 'newest',
      page: 3,
    }, 'Alpha')

    expect(trackerIssues).toHaveBeenCalledWith({
      queue: 'ROBOPARK',
      park: 'Alpha',
      status: 'open',
      robot: '447',
      assignee: 'ivan',
      untagged: undefined,
      age_hours: 24,
      sort: 'newest',
      limit: 50,
      offset: 100,
    })
  })
})
```

- [ ] **Step 5: Run the mapper test and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/work/workData.test.ts
```

Expected: FAIL because `workData.ts` does not exist.

- [ ] **Step 6: Implement the request mapper**

Create `apps/web/src/domains/work/workData.ts`:

```typescript
import { api, type Paged, type TrackerIssue } from '../../api'
import type { WorkUrlState } from './workUrl'

export const WORK_PAGE_SIZE = 50

export type WorkApiClient = Pick<typeof api, 'trackerIssues'>

export function loadWorkPage(
  client: WorkApiClient,
  state: WorkUrlState,
  parkTag: string | undefined,
): Promise<Paged<TrackerIssue>> {
  const { filters } = state
  return client.trackerIssues({
    queue: filters.queue,
    park: filters.untagged ? undefined : parkTag,
    status: filters.status,
    robot: filters.robot,
    assignee: filters.assignee,
    untagged: filters.untagged,
    age_hours: filters.ageHours,
    sort: state.sort,
    limit: WORK_PAGE_SIZE,
    offset: (state.page - 1) * WORK_PAGE_SIZE,
  })
}
```

- [ ] **Step 7: Run GREEN and commit**

```bash
cd apps/web
npx vitest run src/domains/work/workUrl.test.ts src/domains/work/workData.test.ts src/api.test.ts
npm run build
git diff --check
git diff --name-only
git add src/domains/work/workUrl.ts src/domains/work/workUrl.test.ts \
  src/domains/work/workData.ts src/domains/work/workData.test.ts
git commit -m "feat(work): define canonical URL and pagination state"
```

Expected: URL and request-mapping tests pass and the build succeeds.

---

### Task 4: Replace role-specific Tracker screens with one capability-driven `IssueWorkbench`

**Files:**

- Create: `apps/web/src/domains/work/WorkFilters.tsx`
- Create: `apps/web/src/domains/work/WorkFilters.test.tsx`
- Create: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Create: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Create: `apps/web/src/domains/work/WorkPage.tsx`
- Create: `apps/web/src/domains/work/work.css`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.tsx`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.test.tsx`
- Modify: `apps/web/src/components/tracker/IssueDetailPanel.tsx`
- Modify: `apps/web/src/components/tracker/IssueList.tsx`

**Interfaces:**

- Produces: `WorkFiltersProps = { value: WorkUrlState; trackerLogin?: string | null; allowUntagged: boolean; loading: boolean; onApply(next: WorkUrlState): void }`.
- Produces: `IssueWorkbenchApiClient = Pick<typeof api, 'trackerIssues' | 'trackerIssue' | 'trackerComments' | 'trackerTransitions' | 'trackerComment' | 'trackerAttach' | 'trackerAssign' | 'trackerUnassign' | 'trackerTransition' | 'trackerClose'>`.
- Produces: `IssueWorkbenchProps = { apiClient?; user: User; selectedPark: Park; issueKey?: string; state: WorkUrlState; onStateChange(next, options?): void; onOpenIssue(key): void; onCloseIssue(): void; onAuthorizationFailure(): Promise<unknown> }`, where `options` is `{ replace?: boolean }`.
- Produces: `WorkPage({ apiClient? }: { apiClient?: IssueWorkbenchApiClient })`; it is the only route-level controller for both `/work` and `/work/:issueKey`.
- Modifies: `IssueActionsPanel` accepts `capabilities?: TrackerIssueCapabilities`; its existing `canWrite` prop remains optional only as a compile-safe compatibility adapter for legacy pages until Phase 5.
- Uses: Foundation `useParkScope`, `PageLayout`, `Panel`, `Button`, `IconButton`, `LoadingState`, `EmptyState`, `ErrorState`, `StatusBadge`, `ConfirmDialog`; Task 1 `classifyApiError`; Task 3 URL/data helpers.
- Security invariant: persisted Work keys always include `user.id`; only `offline`, `timeout`, and `server` revalidation failures may retain protected cached payload. `unauthorized`/`forbidden` immediately suppress and evict the current user's Work cache, then invoke fail-closed auth refresh; no cached row, detail, comment, transition, or action remains rendered after the denial is observed.

- [ ] **Step 1: Write RED tests for filters and per-action capability rendering**

Create `WorkFilters.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkFilters } from './WorkFilters'

describe('WorkFilters', () => {
  it('applies all form values and resets pagination', () => {
    const onApply = vi.fn()
    render(
      <WorkFilters
        allowUntagged
        loading={false}
        onApply={onApply}
        trackerLogin="ivan"
        value={{ filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 4 }}
      />,
    )
    fireEvent.change(screen.getByLabelText('Статус'), { target: { value: 'open' } })
    fireEvent.change(screen.getByLabelText('Робот'), { target: { value: '447' } })
    fireEvent.change(screen.getByLabelText('Сортировка'), { target: { value: 'newest' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить фильтры' }))

    expect(onApply).toHaveBeenCalledWith({
      filters: { queue: 'ROBOPARK', status: 'open', robot: '447' },
      sort: 'newest',
      page: 1,
    })
  })
})
```

Append to `IssueActionsPanel.test.tsx`:

```tsx
it('renders only capabilities returned for this issue', () => {
  render(
    <IssueActionsPanel
      {...baseProps}
      capabilities={{
        comment: false,
        assign: false,
        unassign: false,
        transition: false,
        close: false,
        attach: true,
      }}
      onAttach={noop}
      onComment={noop}
    />,
  )

  expect(screen.getByText(ru.tracker.attachPhoto)).toBeInTheDocument()
  expect(screen.queryByLabelText(ru.tracker.comments)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
})

it('confirms close, keeps the dialog open on failure, then closes after success', async () => {
  const onClose = vi
    .fn<() => Promise<void>>()
    .mockRejectedValueOnce(new Error('upstream'))
    .mockResolvedValueOnce(undefined)
  render(
    <IssueActionsPanel
      {...baseProps}
      capabilities={{
        comment: true,
        assign: true,
        unassign: true,
        transition: true,
        close: true,
        attach: false,
      }}
      issueKey="ROBOPARK-42"
      onClose={onClose}
      onComment={noop}
    />,
  )

  fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
  expect(onClose).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(ru.tracker.actions.failed)
  expect(screen.getByRole('dialog')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(onClose).toHaveBeenCalledTimes(2)
})
```

- [ ] **Step 2: Run the focused component tests and confirm RED**

```bash
cd apps/web
npx vitest run \
  src/domains/work/WorkFilters.test.tsx \
  src/components/tracker/IssueActionsPanel.test.tsx
```

Expected: FAIL because `WorkFilters` does not exist, `capabilities`/`issueKey` are unknown props and close still calls `window.confirm`.

- [ ] **Step 3: Implement the controlled filters and capability-driven actions**

Implement `WorkFilters.tsx` as a real `<form>` with these controls and serialization rules:

```tsx
export type WorkFiltersProps = {
  value: WorkUrlState
  trackerLogin?: string | null
  allowUntagged: boolean
  loading: boolean
  onApply(next: WorkUrlState): void
}

export function WorkFilters({ value, trackerLogin, allowUntagged, loading, onApply }: WorkFiltersProps) {
  const [draft, setDraft] = useState(value)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onApply({
      filters: {
        ...(draft.filters.queue?.trim() ? { queue: draft.filters.queue.trim() } : {}),
        ...(draft.filters.status?.trim() ? { status: draft.filters.status.trim() } : {}),
        ...(draft.filters.robot?.trim() ? { robot: draft.filters.robot.trim() } : {}),
        ...(draft.filters.assignee?.trim()
          ? { assignee: draft.filters.assignee.trim() }
          : {}),
        ...(allowUntagged && draft.filters.untagged ? { untagged: true } : {}),
        ...(draft.filters.ageHours ? { ageHours: draft.filters.ageHours } : {}),
      },
      sort: draft.sort,
      page: 1,
    })
  }

  return (
    <form className="rp-work-filters" onSubmit={submit}>
      <FormField id="work-queue" label="Очередь" required>
        <input id="work-queue" value={draft.filters.queue ?? ''} onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, queue: event.target.value } })} />
      </FormField>
      <FormField id="work-status" label="Статус">
        <input id="work-status" value={draft.filters.status ?? ''} onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, status: event.target.value } })} />
      </FormField>
      <FormField id="work-robot" label="Робот">
        <input id="work-robot" value={draft.filters.robot ?? ''} onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, robot: event.target.value } })} />
      </FormField>
      <FormField id="work-assignee" label="Ответственный" hint={trackerLogin ? `Мой логин: ${trackerLogin}` : undefined}>
        <input id="work-assignee" value={draft.filters.assignee ?? ''} onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, assignee: event.target.value } })} />
      </FormField>
      <FormField id="work-age" label="Старше, часов">
        <input id="work-age" min="1" inputMode="numeric" type="number" value={draft.filters.ageHours ?? ''} onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, ageHours: event.target.value ? Number(event.target.value) : undefined } })} />
      </FormField>
      <FormField id="work-sort" label="Сортировка">
        <select id="work-sort" value={draft.sort} onChange={(event) => setDraft({ ...draft, sort: event.target.value as WorkSort })}>
          <option value="oldest">Сначала старые</option>
          <option value="newest">Сначала новые</option>
        </select>
      </FormField>
      {allowUntagged && <label><input checked={Boolean(draft.filters.untagged)} type="checkbox" onChange={(event) => setDraft({ ...draft, filters: { ...draft.filters, untagged: event.target.checked } })} /> Без тега парка</label>}
      <Button busy={loading} leadingIcon="filter" type="submit">Применить фильтры</Button>
    </form>
  )
}
```

In `IssueActionsPanel.tsx`, derive the compatibility-safe capability object once:

```tsx
const effectiveCapabilities: TrackerIssueCapabilities = capabilities ?? {
  comment: Boolean(canWrite),
  assign: Boolean(canWrite),
  unassign: Boolean(canWrite),
  transition: Boolean(canWrite),
  close: Boolean(canWrite),
  attach: Boolean(onAttach),
}
```

Gate each form/button group by its own field, replace legacy `.btn` elements with `Button`, add `success` with `aria-live="polite"`, and replace `window.confirm` with the controlled Foundation dialog:

```tsx
const [closeOpen, setCloseOpen] = useState(false)
const [closeError, setCloseError] = useState<string | null>(null)
const [success, setSuccess] = useState('')

const run = async (name: string, action: () => Promise<void>): Promise<boolean> => {
  setBusy(name)
  setError('')
  setSuccess('')
  try {
    await action()
    setSuccess('Действие выполнено')
    return true
  } catch (caught) {
    const message = mapApiError(caught) || ru.tracker.actions.failed
    setError(message)
    if (name === 'close') setCloseError(message)
    return false
  } finally {
    setBusy('')
  }
}

{success && <p aria-live="polite">{success}</p>}
{error && <p role="alert">{error}</p>}
{effectiveCapabilities.close && (
  <Button variant="danger" onClick={() => { setCloseError(null); setCloseOpen(true) }}>
    {ru.tracker.actions.close}
  </Button>
)}
<ConfirmDialog
  cancelLabel="Отмена"
  confirmLabel="Подтвердить закрытие"
  description={`Задача ${issueKey ?? ''} будет закрыта в Tracker и останется в истории.`}
  error={closeError}
  onConfirm={async () => {
    if (await run('close', onClose)) setCloseOpen(false)
  }}
  onOpenChange={setCloseOpen}
  open={closeOpen}
  pending={busy === 'close'}
  title="Закрыть задачу?"
  tone="danger"
/>
```

Preserve file validation, object-URL cleanup, Tracker external-link sanitation and all existing callbacks. Hide `trackerUsers` lookup unless `effectiveCapabilities.assign` is true. Add `issueKey?: string`, `capabilities?: TrackerIssueCapabilities`, and `canWrite?: boolean` to the props type.

- [ ] **Step 4: Run action/filter GREEN before composing the workbench**

```bash
cd apps/web
npx vitest run \
  src/domains/work/WorkFilters.test.tsx \
  src/components/tracker/IssueActionsPanel.test.tsx
```

Expected: both files pass; a failed close stays in the dialog and no unavailable action appears.

- [ ] **Step 5: Write the RED `IssueWorkbench` integration test**

Create `IssueWorkbench.test.tsx` with typed deterministic data:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Park, TrackerIssueDetail, User } from '../../api'
import { IssueWorkbench, type IssueWorkbenchApiClient } from './IssueWorkbench'

const park: Park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK' }
const user: User = {
  id: 3,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  permissions: ['tracker.read', 'tracker.write'],
  parks: [park],
}
const issue: TrackerIssueDetail = {
  key: 'ROBOPARK-42',
  summary: 'Робот не продолжает маршрут',
  status: 'Open',
  queue: 'ROBOPARK',
  robot: '447',
  url: 'https://st.yandex-team.ru/ROBOPARK-42',
  capabilities: {
    comment: true,
    assign: true,
    unassign: true,
    transition: true,
    close: true,
    attach: true,
  },
}

function client(): IssueWorkbenchApiClient {
  return {
    trackerIssues: vi.fn(async () => ({ items: [issue], total: 51, limit: 50, offset: 0, has_more: true })),
    trackerIssue: vi.fn(async () => issue),
    trackerComments: vi.fn(async () => []),
    trackerTransitions: vi.fn(async () => [{ id: 'resolve', display: 'Решить' }]),
    trackerComment: vi.fn(async () => ({ key: issue.key, action: 'comment', status: 'Open', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
    trackerAttach: vi.fn(async () => ({ key: issue.key, action: 'attach', status: 'Open', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
    trackerAssign: vi.fn(async () => ({ key: issue.key, action: 'assign', status: 'Open', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
    trackerUnassign: vi.fn(async () => ({ key: issue.key, action: 'unassign', status: 'Open', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
    trackerTransition: vi.fn(async () => ({ key: issue.key, action: 'transition', status: 'Open', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
    trackerClose: vi.fn(async () => ({ key: issue.key, action: 'close', status: 'Closed', actor: 'operator', performed_at: '2026-09-02T09:00:00Z' })),
  }
}

describe('IssueWorkbench', () => {
  it('loads the URL-selected issue, exposes its robot and paginates through URL state', async () => {
    const apiClient = client()
    const onOpenIssue = vi.fn()
    const onStateChange = vi.fn()
    render(
      <IssueWorkbench
        apiClient={apiClient}
        issueKey="ROBOPARK-42"
        onAuthorizationFailure={vi.fn()}
        onCloseIssue={vi.fn()}
        onOpenIssue={onOpenIssue}
        onStateChange={onStateChange}
        selectedPark={park}
        state={{ filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 1 }}
        user={user}
      />,
    )

    expect(await screen.findByRole('heading', { name: 'Робот не продолжает маршрут' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Проверить робота 447' })).toHaveAttribute('href', '/robots/447/check')
    fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
    expect(onStateChange).toHaveBeenCalledWith(
      { filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 2 },
      { replace: false },
    )
    await waitFor(() => expect(apiClient.trackerIssue).toHaveBeenCalledWith('ROBOPARK-42'))
  })
})
```

Add a stale-while-revalidate regression in the same file: seed `resourceStore` under the current `user.id` with the issue list/detail/comments, make the next load reject with a classified offline/server error, render the workbench, and assert the cached row and selected detail remain visible together with a non-blocking `role="alert"`, `StaleBadge`, and retry action. A cold failure with no cached list must still render the full `ErrorState`.

Add separate seeded-cache `401` and `403` regressions. After the denial resolves, assert the issue title, comments and `IssueActionsPanel` controls are absent, every `work:${user.id}:` key is evicted, a full classified `ErrorState` is present, and `onAuthorizationFailure` is invoked exactly once. Repeat a cache lookup with another synthetic user ID and prove it was neither rendered nor removed. Clear the store and coalescing map after each test.

- [ ] **Step 6: Run the workbench test and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/work/IssueWorkbench.test.tsx
```

Expected: FAIL because `IssueWorkbench.tsx` does not exist.

- [ ] **Step 7: Implement the single resource controller and route glue**

In `IssueWorkbench.tsx`, use these exact cache keys and request rules:

```typescript
const cachePrefix = `work:${user.id}:`
const listKey = `${cachePrefix}list:${selectedPark.id}:${JSON.stringify(state)}`
const list = useCachedResource(listKey, () => loadWorkPage(apiClient, state, selectedPark.tag))
const detail = useCachedResource(
  issueKey ? `${cachePrefix}issue:${issueKey}` : '',
  () => apiClient.trackerIssue(issueKey as string),
  { enabled: Boolean(issueKey) },
)
const comments = useCachedResource(
  issueKey ? `${cachePrefix}comments:${issueKey}` : '',
  () => apiClient.trackerComments(issueKey as string),
  { enabled: Boolean(issueKey) },
)
const transitions = useCachedResource(
  issueKey && detail.data?.capabilities.transition ? `${cachePrefix}transitions:${issueKey}` : '',
  () => apiClient.trackerTransitions(issueKey as string),
  { enabled: Boolean(issueKey && detail.data?.capabilities.transition) },
)
```

Render exactly one list pane and one detail pane. Mount `WorkFilters` with `key={buildWorkSearch(state, null)}` so incoming URL changes reset its draft without an effect loop. `IssueList.onSelect` calls `onOpenIssue`; previous/next buttons change `page` by one and never accumulate pages in memory. On every successful mutation invalidate/refresh only `${cachePrefix}list:${selectedPark.id}:`, `${cachePrefix}issue:${issueKey}`, `${cachePrefix}comments:${issueKey}` and `${cachePrefix}transitions:${issueKey}`. Use the detail response's `capabilities` in `IssueActionsPanel`, and change `RobotCheckPanel`/the detail robot link to `/robots/${encodeURIComponent(issue.robot)}/check` with accessible name `Проверить робота ${issue.robot}`. Task 8 resolves a short number and replaces this transitional URL with the canonical VIN without adding an extra user action.

Map resource state without collapsing meanings. A failed revalidation must not replace usable cached work:

```tsx
const listFailure = list.error
  ? classifyApiError(list.error, 'Не удалось загрузить очередь задач.')
  : null
const canRetainProtectedData = (failure: DomainError | null) =>
  failure != null && ['offline', 'timeout', 'server'].includes(failure.kind)
const listBlocked = listFailure != null && !canRetainProtectedData(listFailure)

if (listFailure && (!list.data || listBlocked)) {
  return <ErrorState title={listFailure.title} description={listFailure.description} requestId={listFailure.requestId} onRetry={listFailure.retryable ? list.refresh : undefined} />
}
if (!list.isLoading && !listFailure && list.data?.items.length === 0) {
  return <EmptyState title="Нет задач" description="Измените фильтры или проверьте выбранный парк." icon="work" />
}
```

In the normal list/detail layout, keep rows/detail/comments mounted only when the classified failure passes `canRetainProtectedData` and cached data exists; then render a compact warning with `role="alert"`, a `StaleBadge` (`offline` for browser-offline, otherwise `stale`), the safe description/request ID, and retry. `configuration`, `unknown`, `conflict`, `not-found`, `unauthorized`, and `forbidden` never reuse protected payload; they render their full local `ErrorState`, and actions stay unmounted. On a `not-found`, evict the affected detail keys. On `unauthorized` or `forbidden`, an effect immediately invalidates `cachePrefix` with `{ prefix: true }` and calls `void onAuthorizationFailure().catch(() => undefined)` once for that observed error object; render suppression is synchronous and does not wait for the effect. An offline/timeout/server failure never clears selected issue or list scroll.

Use a separate `ErrorState` for detail `403`, `404`, `409` and server failures. Keep the list visible on widths `>=900px`; on widths `<=899px`, hide it while `issueKey` is present and show an explicit back link. Restore list scroll in `useLayoutEffect` from Task 3 and save it before `onOpenIssue` and on unmount.

Implement `WorkPage.tsx` with Foundation route/park state:

```tsx
export function WorkPage({ apiClient = api }: { apiClient?: IssueWorkbenchApiClient }) {
  const { user, refreshUser } = useAuth()
  const { issueKey } = useParams<{ issueKey?: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { parkId, selectedPark, loading } = useParkScope()

  if (!user) return null
  if (loading) return <LoadingState label="Загружаем область работы" variant="page" />
  if (!selectedPark || parkId == null) {
    return <EmptyState title="Парк не выбран" description="Выберите доступный парк, чтобы открыть очередь." icon="parks" />
  }
  const queue = selectedPark.tracker_queue?.trim()
  if (!queue) {
    return <EmptyState title="Очередь не настроена" description={`Для парка «${selectedPark.name}» не указана очередь Tracker.`} icon="warning" />
  }
  const state = parseWorkUrl(params, { queue })
  const writeState = (next: WorkUrlState, options: { replace?: boolean } = {}) =>
    navigate({ pathname: issueKey ? `/work/${encodeURIComponent(issueKey)}` : '/work', search: buildWorkSearch(next, parkId) }, { replace: options.replace })

  return (
    <PageLayout title="Работа" description={`Парк: ${selectedPark.name} · очередь ${queue}`}>
      <IssueWorkbench
        apiClient={apiClient}
        issueKey={issueKey}
        onCloseIssue={() => navigate(workListHref(state, parkId))}
        onOpenIssue={(key) => navigate(workIssueHref(key, state, parkId))}
        onStateChange={writeState}
        onAuthorizationFailure={refreshUser}
        selectedPark={selectedPark}
        state={state}
        user={user}
      />
    </PageLayout>
  )
}
```

Inside `IssueWorkbench`, use `window.location.search` as the Task 3 scroll-storage key. Do not use role branches to choose another workspace.

- [ ] **Step 8: Add the responsive domain stylesheet**

In `work.css`, use only Foundation tokens and these layout invariants:

```css
.rp-workbench { min-width: 0; display: grid; gap: var(--rp-space-4); }
.rp-work-list-pane, .rp-work-detail-pane { min-width: 0; }
.rp-work-filters { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--rp-space-3); }
.rp-work-pagination { display: flex; align-items: center; justify-content: space-between; gap: var(--rp-space-2); }
.rp-work-detail-back { display: none; }

@media (max-width: 599px) {
  .rp-work-filters { grid-template-columns: 1fr; }
  .rp-workbench[data-has-detail='true'] .rp-work-list-pane { display: none; }
  .rp-workbench[data-has-detail='false'] .rp-work-detail-pane { display: none; }
  .rp-work-detail-back { display: inline-flex; min-height: 44px; }
}
@media (min-width: 600px) and (max-width: 899px) {
  .rp-work-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rp-workbench[data-has-detail='true'] .rp-work-list-pane { display: none; }
  .rp-workbench[data-has-detail='false'] .rp-work-detail-pane { display: none; }
  .rp-work-detail-back { display: inline-flex; min-height: 44px; }
}
@media (min-width: 900px) and (max-width: 1199px) {
  .rp-workbench { grid-template-columns: minmax(20rem, 42%) minmax(0, 1fr); }
  .rp-work-filters { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (min-width: 1200px) {
  .rp-workbench { grid-template-columns: minmax(22rem, 38%) minmax(0, 1fr); }
  .rp-work-filters { grid-template-columns: repeat(6, minmax(0, 1fr)); }
}
```

Add `overflow-wrap: anywhere` to issue summaries/URLs, `min-width: 0` to every grid child, `min-height: 44px` to row buttons on phone, token-based borders/surfaces/focus and `@media (prefers-reduced-motion: reduce)` disabling transitions. Do not add horizontal scrolling to the page or filter toolbar.

- [ ] **Step 9: Run the Work domain regression set and commit**

```bash
cd apps/web
npx vitest run \
  src/domains/work/workUrl.test.ts \
  src/domains/work/workData.test.ts \
  src/domains/work/WorkFilters.test.tsx \
  src/domains/work/IssueWorkbench.test.tsx \
  src/components/tracker/IssueActionsPanel.test.tsx \
  src/components/tracker/RobotCheckPanel.test.tsx \
  src/components/tracker/issue-utils.test.ts
npm run build
git diff --check
git diff --name-only
git add \
  src/domains/work/WorkFilters.tsx \
  src/domains/work/WorkFilters.test.tsx \
  src/domains/work/IssueWorkbench.tsx \
  src/domains/work/IssueWorkbench.test.tsx \
  src/domains/work/WorkPage.tsx \
  src/domains/work/work.css \
  src/components/tracker/IssueActionsPanel.tsx \
  src/components/tracker/IssueActionsPanel.test.tsx \
  src/components/tracker/IssueDetailPanel.tsx \
  src/components/tracker/IssueList.tsx
git commit -m "feat(work): introduce unified issue workbench"
```

Expected: all Work and touched Tracker tests pass, TypeScript builds, and only the listed Work/Tracker files are staged.

---

### Task 5: Build the role-aware Shift/Overview from current operational data

**Files:**

- Create: `apps/web/src/domains/shift/overviewData.ts`
- Create: `apps/web/src/domains/shift/overviewData.test.ts`
- Create: `apps/web/src/domains/shift/overviewModel.ts`
- Create: `apps/web/src/domains/shift/overviewModel.test.ts`
- Create: `apps/web/src/domains/shift/OverviewSections.tsx`
- Create: `apps/web/src/domains/shift/OverviewPage.tsx`
- Create: `apps/web/src/domains/shift/OverviewPage.test.tsx`
- Create: `apps/web/src/domains/shift/overview.css`

**Interfaces:**

- Produces: `OverviewApiClient = Pick<typeof api, 'dashboardSummary' | 'mechanicTasks' | 'operatorBlockers' | 'trackerIssues'>`.
- Produces: discriminated `OverviewPayload`: `{ kind: 'driver' }`, `{ kind: 'park'; summary; issues }`, or `{ kind: 'fleet'; summaries }`.
- Produces: `loadOverview(apiClient, user, selectedPark, parks): Promise<OverviewPayload>`; driver makes no Tracker/dashboard request, royal loads current summaries for all active accessible parks, and other roles load only the selected park.
- Produces: `OverviewViewModel = { scope; updatedAt; freshness; state; risk; primaryAction; queue; metrics }` with typed tones and links; `buildOverviewModel(input, now): OverviewViewModel` is pure.
- Produces: `OverviewPage({ apiClient? })`; it consumes `useAuth`, `useParkScope`, Foundation async/layout/status primitives and never calls `dashboardHistory` or `operatorNowReport`.
- Ownership: `domains/shift/*` is the only Overview implementation. Phase 3 may link to it but must not add historical charts or cross-park comparisons here.

- [ ] **Step 1: Write RED tests for role-to-source selection**

Create `overviewData.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import type { DashboardSummary, Park, User } from '../../api'
import { loadOverview, type OverviewApiClient } from './overviewData'

const alpha: Park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK', is_active: true }
const beta: Park = { id: 8, name: 'Юг', tag: 'Beta', tracker_queue: 'ROBOPARK', is_active: true }
const summary = (parkId: number): DashboardSummary => ({
  park_id: parkId,
  generated_at: '2026-09-02T09:00:00Z',
  arrived: 1,
  done: 2,
  queued: 3,
  in_transit: 1,
  moving: [],
})
const user = (role: User['role']): User => ({
  id: 1,
  username: role,
  role,
  access_status: 'approved',
  permissions: role === 'driver' ? ['nav.dashboard', 'nav.robot_search', 'nav.emergency'] : ['nav.dashboard', 'tracker.read'],
  parks: [alpha, beta],
})

function client(): OverviewApiClient {
  return {
    dashboardSummary: vi.fn(async (parkId: number) => summary(parkId)),
    mechanicTasks: vi.fn(async () => ({ park_tag: 'Alpha', status: 'all', counts: {}, items: [] })),
    operatorBlockers: vi.fn(async () => ({ park_id: 7, park_tag: 'Alpha', status: 'all', counts: {}, items: [] })),
    trackerIssues: vi.fn(async () => ({ items: [], total: 0, limit: 5, offset: 0, has_more: false })),
  }
}

describe('loadOverview', () => {
  it.each([
    ['mechanic', 'mechanicTasks'],
    ['operator', 'operatorBlockers'],
    ['admin', 'trackerIssues'],
  ] as const)('loads the %s operational queue through %s', async (role, method) => {
    const apiClient = client()
    const result = await loadOverview(apiClient, user(role), alpha, [alpha, beta])
    expect(result.kind).toBe('park')
    expect(apiClient.dashboardSummary).toHaveBeenCalledWith(7)
    expect(apiClient[method]).toHaveBeenCalledTimes(1)
  })

  it('loads every active park summary for the owner view', async () => {
    const apiClient = client()
    const result = await loadOverview(apiClient, user('royal'), alpha, [alpha, beta])
    expect(result.kind).toBe('fleet')
    expect(apiClient.dashboardSummary).toHaveBeenCalledTimes(2)
    expect(apiClient.dashboardSummary).toHaveBeenNthCalledWith(1, 7)
    expect(apiClient.dashboardSummary).toHaveBeenNthCalledWith(2, 8)
  })

  it('does not request park or Tracker data for a driver', async () => {
    const apiClient = client()
    expect(await loadOverview(apiClient, user('driver'), alpha, [alpha])).toEqual({ kind: 'driver' })
    expect(apiClient.dashboardSummary).not.toHaveBeenCalled()
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run the loader test and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/shift/overviewData.test.ts
```

Expected: FAIL because `overviewData.ts` does not exist.

- [ ] **Step 3: Implement bounded role-specific loading**

Create `overviewData.ts` with this public shape and exact calls:

```typescript
import { api, type Blocker, type DashboardSummary, type Park, type TrackerIssue, type User } from '../../api'

export type OverviewApiClient = Pick<
  typeof api,
  'dashboardSummary' | 'mechanicTasks' | 'operatorBlockers' | 'trackerIssues'
>

export type OverviewPayload =
  | { kind: 'driver' }
  | { kind: 'park'; park: Park; summary: DashboardSummary; issues: Array<Blocker | TrackerIssue> }
  | { kind: 'fleet'; summaries: Array<{ park: Park; summary: DashboardSummary }> }

export async function loadOverview(
  client: OverviewApiClient,
  user: User,
  selectedPark: Park | null,
  parks: Park[],
): Promise<OverviewPayload> {
  if (user.role === 'driver') return { kind: 'driver' }
  if (user.role === 'royal') {
    const active = parks.filter((park) => park.is_active !== false)
    const summaries = await Promise.all(
      active.map(async (park) => ({ park, summary: await client.dashboardSummary(park.id) })),
    )
    return { kind: 'fleet', summaries }
  }
  if (!selectedPark) throw new Error('overview_park_required')

  const summary = await client.dashboardSummary(selectedPark.id)
  if (user.role === 'mechanic') {
    const queue = await client.mechanicTasks('all', selectedPark.id)
    return { kind: 'park', park: selectedPark, summary, issues: queue.items.slice(0, 5) }
  }
  if (user.role === 'operator') {
    const queue = await client.operatorBlockers(selectedPark.id, 'all')
    return { kind: 'park', park: selectedPark, summary, issues: queue.items.slice(0, 5) }
  }
  const queue = await client.trackerIssues({
    queue: selectedPark.tracker_queue?.trim() || undefined,
    park: selectedPark.tag?.trim() || undefined,
    sort: 'oldest',
    limit: 5,
    offset: 0,
  })
  return { kind: 'park', park: selectedPark, summary, issues: queue.items }
}
```

The royal branch is bounded to the already authenticated `parks` array, filters inactive parks, and labels the result as “all accessible active parks”; it does not claim global visibility beyond that scope.

- [ ] **Step 4: Write RED tests for the fixed semantic block order and all five roles**

Create `overviewModel.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import type { DashboardSummary, Park } from '../../api'
import { buildOverviewModel } from './overviewModel'

const park: Park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK' }
const summary: DashboardSummary = {
  park_id: 7,
  generated_at: '2026-09-02T09:00:00Z',
  arrived: 2,
  done: 4,
  queued: 3,
  in_transit: 1,
  moving: [{ key: 'ROBOPARK-42', summary: 'Робот 447 остановился' }],
}
const now = new Date('2026-09-02T09:03:00Z')

describe('buildOverviewModel', () => {
  it.each([
    ['mechanic', 'Что требует внимания в смене', 'Открыть задачу ROBOPARK-42'],
    ['operator', 'Что мешает работе парка', 'Разобрать ROBOPARK-42'],
    ['admin', 'Готовность людей и системы', 'Открыть работу'],
  ] as const)('builds the %s hierarchy', (role, stateTitle, actionLabel) => {
    const model = buildOverviewModel({ role, payload: { kind: 'park', park, summary, issues: [] }, parkId: 7 }, now)
    expect(model.state.title).toBe(stateTitle)
    expect(model.primaryAction.label).toBe(actionLabel)
    expect(model.updatedAt).toBe(summary.generated_at)
    expect(model.freshness).toBe('fresh')
  })

  it('gives the driver a robot-first path without invented freshness', () => {
    const model = buildOverviewModel({ role: 'driver', payload: { kind: 'driver' }, parkId: null }, now)
    expect(model.scope).toContain('проверка конкретного робота')
    expect(model.updatedAt).toBeNull()
    expect(model.primaryAction).toEqual({ label: 'Найти или сканировать робота', href: '/robots', icon: 'scan' })
  })

  it('uses a generic permission-safe hierarchy for a custom role slug', () => {
    const model = buildOverviewModel({
      role: 'field_lead',
      payload: { kind: 'park', park, summary, issues: [] },
      parkId: 7,
    }, now)
    expect(model.state.title).toBe('Что требует внимания сейчас')
    expect(model.primaryAction).toEqual({
      label: 'Открыть ROBOPARK-42',
      href: '/work/ROBOPARK-42?park=7&queue=ROBOPARK&sort=oldest&page=1',
      icon: 'work',
    })
  })

  it('names the highest current owner queue and accessible scope', () => {
    const model = buildOverviewModel({
      role: 'royal',
      parkId: 7,
      payload: {
        kind: 'fleet',
        summaries: [
          { park, summary },
          { park: { ...park, id: 8, name: 'Юг', tag: 'Beta' }, summary: { ...summary, park_id: 8, queued: 9 } },
        ],
      },
    }, now)
    expect(model.scope).toBe('Все доступные активные парки · 2')
    expect(model.risk?.title).toBe('Наибольшая текущая очередь: Юг · 9')
  })
})
```

- [ ] **Step 5: Run the model test and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/shift/overviewModel.test.ts
```

Expected: FAIL because the pure model does not exist.

- [ ] **Step 6: Implement the pure `state → risk → next action` model**

Create explicit exported types in `overviewModel.ts`:

```typescript
export type OverviewAction = { label: string; href: string; icon: IconName }
export type OverviewRisk = { tone: StatusTone; title: string; description: string; issueKey?: string }
export type OverviewQueueItem = { key: string; summary: string; robot?: string | null; href: string }
export type OverviewViewModel = {
  scope: string
  updatedAt: string | null
  freshness: Freshness | null
  state: { title: string; description: string; tone: StatusTone }
  risk: OverviewRisk | null
  primaryAction: OverviewAction
  queue: OverviewQueueItem[]
  metrics: Array<{ label: string; value: number }>
}
```

Implement `freshness` as `live` for age `<=30s`, `fresh` for age `<=5min`, and `stale` above five minutes; use the oldest `generated_at` for a multi-park owner view. For park payloads, prefer `summary.moving[0]` as the next actionable issue, then `issues[0]`, then `/work`. Construct issue links with `workIssueHref(key, { filters: { queue: park.tracker_queue || undefined }, sort: 'oldest', page: 1 }, park.id)`. Use these fixed system-role labels and a guarded custom-role fallback:

```typescript
const ROLE_STATE_TITLE: Record<UserRole, string> = {
  mechanic: 'Что требует внимания в смене',
  operator: 'Что мешает работе парка',
  driver: 'Можно ли безопасно продолжать работу',
  admin: 'Готовность людей и системы',
  royal: 'Главный риск доступных парков',
}

export function overviewStateTitle(role: string): string {
  return isSystemUserRole(role)
    ? ROLE_STATE_TITLE[role]
    : 'Что требует внимания сейчас'
}
```

`buildOverviewModel` accepts `role: string`, imports `isSystemUserRole`, and never indexes `ROLE_STATE_TITLE` with an unguarded API value. Mechanic primary label is `Открыть задачу ${key}`, operator is `Разобрать ${key}`, admin without a task is `Открыть работу`, driver is exactly the action in the test, royal links its highest-queue park to `/work?park=${highest.park.id}`, and a custom role uses neutral `Открыть ${key}` or `Открыть работу`; route access remains permission-driven. Missing `tracker_queue` or `tag` yields a warning risk “Парк не готов к работе с Tracker” and an `/admin` action only for a user whom the current manifest allows into Administration; every other role sees text-only guidance to contact an administrator. Do not label non-zero counts as a failure. Metrics are current `Пришли`, `Завершены`, `В очереди`, `В пути`; no trend or SLA is calculated.

- [ ] **Step 7: Write a RED page-level state test**

Create `OverviewPage.test.tsx`. Its local wrapper must use the real contexts, with no hook mocks:

```tsx
function renderOverview({
  role,
  parkId,
  apiClient,
}: {
  role: string
  parkId: number
  apiClient: OverviewApiClient
}) {
  const park: Park = { id: parkId, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK' }
  const user: User = {
    id: 3,
    username: role,
    role,
    access_status: 'approved',
    permissions: ['nav.dashboard', 'tracker.read'],
    parks: [park],
  }
  return render(
    <MemoryRouter initialEntries={[`/overview?park=${parkId}`]}>
      <AuthContext.Provider value={{
        user,
        loading: false,
        login: async () => user,
        refreshUser: async () => user,
        logout: async () => undefined,
      }}>
        <ParkScopeProvider>
          <OverviewPage apiClient={apiClient} />
        </ParkScopeProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}
```

Import `AuthContext`, `ParkScopeProvider`, `MemoryRouter`, Testing Library, and the API/domain types. Keep the wrapper role as raw `string` so the test can render a Phase 4 custom role. Define `operatorClient` with all four `OverviewApiClient` methods and fixed payloads from Step 1; define `failingClient(error)` by overriding only `dashboardSummary` to reject. Then add:

```tsx
it('renders scope, state, risk, action and queue in semantic order', async () => {
  renderOverview({ role: 'operator', parkId: 7, apiClient: operatorClient })

  const scope = await screen.findByTestId('overview-scope')
  const state = screen.getByTestId('overview-state')
  const risk = screen.getByTestId('overview-risk')
  const action = screen.getByTestId('overview-action')
  const queue = screen.getByTestId('overview-queue')
  expect(scope.compareDocumentPosition(state) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(state.compareDocumentPosition(risk) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(risk.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(action.compareDocumentPosition(queue) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getByText('Парк: Север')).toBeInTheDocument()
  expect(screen.queryByText(/истори|тренд/i)).not.toBeInTheDocument()
})

it('shows a local retryable error with request id', async () => {
  renderOverview({ role: 'operator', parkId: 7, apiClient: failingClient(new ApiError(502, null, 'req-overview')) })
  expect(await screen.findByText('Сервис временно недоступен')).toBeInTheDocument()
  expect(screen.getByText('req-overview')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
})
```

- [ ] **Step 8: Implement sections, page states and responsive hierarchy**

`OverviewSections.tsx` exports `OverviewScope`, `OverviewState`, `OverviewRiskCard`, `OverviewPrimaryAction`, `OverviewQueue`, and `OverviewMetrics`. Each receives only the corresponding `OverviewViewModel` field. Scope renders `StaleBadge` only when `freshness` and `updatedAt` are non-null. Queue links use `<Link>` and retain numeric park state.

`OverviewPage.tsx` must:

1. return `LoadingState` while `ParkScopeProvider` resolves;
2. render the driver model without requiring a park;
3. render `EmptyState` titled “Парк не выбран” for non-driver/non-royal users without scope;
4. load via `useCachedResource` key `overview:${user.id}:${user.role}:${parkId ?? 'fleet'}`;
5. map failures with `classifyApiError`, preserve `requestId`, and pass retry only when `retryable` is true;
6. render sections in exact spec order: scope, state, risk when present, primary action, queue, then metrics;
7. include no imports or calls containing `dashboardHistory` or `operatorNowReport`.

Use this structural CSS in `overview.css`:

```css
.rp-overview { display: grid; gap: var(--rp-space-4); min-width: 0; }
.rp-overview-primary { position: sticky; bottom: calc(64px + var(--rp-space-2)); z-index: 2; }
.rp-overview-queue, .rp-overview-metrics { min-width: 0; }
.rp-overview-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--rp-space-3); }

@media (min-width: 900px) {
  .rp-overview { grid-template-columns: minmax(0, 0.9fr) minmax(22rem, 1.1fr); align-items: start; }
  .rp-overview-scope, .rp-overview-state, .rp-overview-risk, .rp-overview-primary { grid-column: 1; }
  .rp-overview-queue { grid-column: 2; grid-row: 1 / span 5; }
  .rp-overview-metrics { grid-column: 1 / -1; grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .rp-overview-primary { position: static; }
}
```

At `<=599px`, ensure scope, risk title and primary action appear before the first viewport's long queue; buttons have at least 44px height. At `600–899px`, keep the same semantic order with two-column metrics. At `900–1199px` and `>=1200px`, show state and queue concurrently. Use only `--rp-*` tokens and disable nonessential motion for reduced-motion users.

- [ ] **Step 9: Run Shift/Overview GREEN and commit**

```bash
cd apps/web
npx vitest run \
  src/domains/shift/overviewData.test.ts \
  src/domains/shift/overviewModel.test.ts \
  src/domains/shift/OverviewPage.test.tsx \
  src/domains/work/workUrl.test.ts
npm run build
git diff --check
git diff --name-only
git add \
  src/domains/shift/overviewData.ts \
  src/domains/shift/overviewData.test.ts \
  src/domains/shift/overviewModel.ts \
  src/domains/shift/overviewModel.test.ts \
  src/domains/shift/OverviewSections.tsx \
  src/domains/shift/OverviewPage.tsx \
  src/domains/shift/OverviewPage.test.tsx \
  src/domains/shift/overview.css
git commit -m "feat(overview): add role-aware operational shift view"
```

Expected: all five roles are covered by loader/model tests, page states pass, the build succeeds and no Analytics/history file changed.

---

### Task 6: Add the user-scoped `RobotResolver`, recent robots and progressive scanner

**Files:**

- Create: `apps/web/src/domains/robots/robotReference.ts`
- Create: `apps/web/src/domains/robots/robotReference.test.ts`
- Create: `apps/web/src/domains/robots/recentRobots.ts`
- Create: `apps/web/src/domains/robots/recentRobots.test.ts`
- Create: `apps/web/src/domains/robots/RobotScanner.tsx`
- Create: `apps/web/src/domains/robots/RobotScanner.test.tsx`
- Create: `apps/web/src/domains/robots/RobotResolver.tsx`
- Create: `apps/web/src/domains/robots/RobotResolver.test.tsx`
- Create: `apps/web/src/domains/robots/RobotsPage.tsx`
- Create: `apps/web/src/domains/robots/robots.css`
- Consume unchanged: `apps/web/src/shared/auth/protectedBrowserStorage.ts`
- Consume unchanged until Phase 5: `apps/web/src/lib/recentRobots.ts`

**Interfaces:**

- Produces: `parseRobotReference(raw): string | null`; accepts a short number, VIN, `/robots/:id`, `/robots/:id/check`, or `/emergency?q|robot=...`, and performs no network request.
- Produces: `RecentRobot = { query: string; vin: string; openedAt: number }`; `RECENT_ROBOT_TTL_MS = 2_592_000_000`; `loadRecentRobots(userId, now?)`; `rememberRobot(userId, resolved, now?)`; `clearRecentRobots(userId)`. Its key builder consumes the shared `RECENT_ROBOTS_V2_STORAGE_PREFIX`; the per-user clear remains a visible user action, while auth session boundaries use prefix-wide `clearProtectedBrowserStorage()`.
- Produces: `RobotResolverApiClient = Pick<typeof api, 'emergencyResolve'>`; `RobotResolverProps = { userId; value; apiClient?; onValueChange; onResolved(result); mode?: 'search' | 'inline' }`.
- Produces: `RobotScannerProps = { open; onCancel(); onDetected(value); mediaDevices?; Detector? }` and local `BarcodeDetectorLike`/`BarcodeDetectorConstructor` types, avoiding ambient experimental DOM types.
- Produces: `RobotsPage({ apiClient? })`; its URL search state is `q`, successful resolution navigates to canonical `/robots/:vin`, and it never queries Tracker just to resolve identity.
- Compatibility rule: do not migrate or read the legacy unscoped `robopark.recentRobots` key; only the v2 user key is displayed.

- [ ] **Step 1: Write RED parsing and user-scoped retention tests**

Create `robotReference.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { parseRobotReference } from './robotReference'

describe('parseRobotReference', () => {
  it.each([
    ['447', '447'],
    ['a1555', 'a1555'],
    ['YASADR00000001975', 'YASADR00000001975'],
    ['/robots/YASADR00000001975', 'YASADR00000001975'],
    ['https://robopark.example/robots/447/check?tab=map', '447'],
    ['/emergency?q=1555&tab=wheels', '1555'],
    ['/emergency?robot=1666', '1666'],
  ])('extracts %s', (raw, expected) => {
    expect(parseRobotReference(raw)).toBe(expected)
  })

  it.each(['', '/robots', '/emergency?tab=map', 'https://example.test/not-a-robot', 'robot'])('rejects %s', (raw) => {
    expect(parseRobotReference(raw)).toBeNull()
  })
})
```

Create `recentRobots.test.ts`:

```typescript
import { afterEach, describe, expect, it } from 'vitest'
import {
  clearRecentRobots,
  loadRecentRobots,
  RECENT_ROBOT_TTL_MS,
  rememberRobot,
} from './recentRobots'

describe('recent robots v2', () => {
  afterEach(() => localStorage.clear())

  it('isolates users, deduplicates by VIN and keeps newest first', () => {
    rememberRobot(7, { query: '447', vin: 'YASADR00000000447' }, 1_000)
    rememberRobot(8, { query: '888', vin: 'YASADR00000000888' }, 2_000)
    rememberRobot(7, { query: 'YASADR00000000447', vin: 'yasadr00000000447' }, 3_000)

    expect(loadRecentRobots(7, 3_001)).toEqual([
      { query: 'YASADR00000000447', vin: 'YASADR00000000447', openedAt: 3_000 },
    ])
    expect(loadRecentRobots(8, 3_001)).toHaveLength(1)
  })

  it('drops expired entries, caps at six and clears only the current user', () => {
    for (let index = 0; index < 7; index += 1) {
      rememberRobot(7, { query: String(index), vin: `YASADR${String(index).padStart(11, '0')}` }, index + 100)
    }
    localStorage.setItem('robopark.recentRobots', JSON.stringify(['legacy-447']))
    expect(loadRecentRobots(7, 200)).toHaveLength(6)
    expect(loadRecentRobots(7, RECENT_ROBOT_TTL_MS + 10_000)).toEqual([])
    clearRecentRobots(7)
    expect(localStorage.getItem('robopark.recentRobots')).toBe('["legacy-447"]')
  })
})
```

- [ ] **Step 2: Run parser/storage tests and confirm RED**

```bash
cd apps/web
npx vitest run \
  src/domains/robots/robotReference.test.ts \
  src/domains/robots/recentRobots.test.ts
```

Expected: FAIL because both domain utilities are absent.

- [ ] **Step 3: Implement strict local parsing and v2 storage**

Create `robotReference.ts`:

```typescript
const ROBOT_VALUE = /^(?:[a-z]\d+|\d+|yasadr[\d\s-]+)$/i

function valid(value: string | null): string | null {
  const trimmed = value?.trim() ?? ''
  return trimmed.length > 0 && trimmed.length <= 64 && ROBOT_VALUE.test(trimmed) ? trimmed : null
}

export function parseRobotReference(raw: string): string | null {
  const input = raw.trim()
  if (!input) return null
  if (!input.includes('/') && !input.includes('?')) return valid(input)
  let url: URL
  try {
    url = new URL(input, 'https://robopark.local')
  } catch {
    return null
  }
  const robotRoute = url.pathname.match(/^\/robots\/([^/]+)(?:\/check)?\/?$/)
  if (robotRoute) return valid(decodeURIComponent(robotRoute[1]))
  if (url.pathname === '/emergency' || url.pathname === '/emergency/') {
    return valid(url.searchParams.get('q') ?? url.searchParams.get('robot'))
  }
  return null
}
```

Create `recentRobots.ts` using key `${RECENT_ROBOTS_V2_STORAGE_PREFIX}${userId}` imported from `shared/auth/protectedBrowserStorage.ts`, JSON validation for every field, uppercase VIN normalization, six-entry maximum and exact expiry predicate `now - openedAt < RECENT_ROBOT_TTL_MS`. Storage exceptions return an empty list and never break navigation. `rememberRobot` writes only a successful `{ query, vin }` pair; it does not accept unresolved input. Ordinary reload/offline does not call global cleanup and therefore retains valid same-session recents; only explicit per-user clear or an auth session boundary removes them.

- [ ] **Step 4: Write RED scanner cleanup tests**

Create `RobotScanner.test.tsx`:

```tsx
import { act, render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RobotScanner, type BarcodeDetectorConstructor } from './RobotScanner'

describe('RobotScanner', () => {
  it('starts only while open and stops every camera track after detection', async () => {
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const detect = vi.fn(async () => [{ rawValue: 'YASADR00000001975' }])
    const Detector = class { detect = detect } as unknown as BarcodeDetectorConstructor
    const onDetected = vi.fn()
    const frame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      void callback(1)
      return 1
    })

    render(
      <RobotScanner
        Detector={Detector}
        mediaDevices={{ getUserMedia } as MediaDevices}
        onCancel={vi.fn()}
        onDetected={onDetected}
        open
      />,
    )

    await waitFor(() => expect(onDetected).toHaveBeenCalledWith('YASADR00000001975'))
    expect(getUserMedia).toHaveBeenCalledWith({ video: { facingMode: { ideal: 'environment' } }, audio: false })
    expect(stop).toHaveBeenCalledTimes(1)
    frame.mockRestore()
  })

  it('stops tracks when unmounted before a result', async () => {
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
    const view = render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as MediaDevices} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    await act(async () => undefined)
    view.unmount()
    expect(stop).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 5: Run the scanner test and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/robots/RobotScanner.test.tsx
```

Expected: FAIL because `RobotScanner.tsx` does not exist.

- [ ] **Step 6: Implement progressive camera detection with unconditional cleanup**

Define local types and feature detection:

```tsx
export type BarcodeDetectorLike = { detect(source: ImageBitmapSource): Promise<Array<{ rawValue?: string }>> }
export type BarcodeDetectorConstructor = new (options?: { formats?: string[] }) => BarcodeDetectorLike
type DetectorWindow = Window & { BarcodeDetector?: BarcodeDetectorConstructor }

export function scannerSupported(): boolean {
  return Boolean((window as DetectorWindow).BarcodeDetector && navigator.mediaDevices?.getUserMedia)
}
```

`RobotScanner` renders a Foundation `BottomSheet` with a `<video muted playsInline>` and “Отменить”. When `open` becomes true, call `getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })`, assign `video.srcObject`, call `video.play()`, then run one detector pass per `requestAnimationFrame`. A non-empty `rawValue` calls `onDetected` once and immediately runs:

```typescript
const stopCamera = () => {
  cancelAnimationFrame(frameRef.current)
  streamRef.current?.getTracks().forEach((track) => track.stop())
  streamRef.current = null
  if (videoRef.current) videoRef.current.srcObject = null
}
```

Call `stopCamera` on detection, cancel, `open=false`, detector/camera error and effect cleanup. Show camera errors in `role="alert"` and keep a “Ввести номер вручную” action that calls `onCancel`; never auto-request permission during page mount.

- [ ] **Step 7: Write the RED resolver interaction test**

Create `RobotResolver.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RobotResolver } from './RobotResolver'
import { loadRecentRobots } from './recentRobots'

describe('RobotResolver', () => {
  afterEach(() => {
    vi.useRealTimers()
    localStorage.clear()
  })

  it('normalizes a pasted deep link and reports a resolved VIN', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-02T09:00:00Z'))
    const emergencyResolve = vi.fn(async () => ({ vin: 'YASADR00000001975', sections: [] }))
    const onResolved = vi.fn()
    const onValueChange = vi.fn()
    render(
      <RobotResolver
        apiClient={{ emergencyResolve }}
        onResolved={onResolved}
        onValueChange={onValueChange}
        userId={7}
        value="https://robopark.example/emergency?q=1975&tab=map"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
    await waitFor(() => expect(emergencyResolve).toHaveBeenCalledWith('1975'))
    expect(onResolved).toHaveBeenCalledWith({ vin: 'YASADR00000001975', sections: [] })
    expect(loadRecentRobots(7, Date.now())).toEqual(expect.arrayContaining([
      expect.objectContaining({ vin: 'YASADR00000001975' }),
    ]))
  })

  it('keeps manual input visible when scanning is unsupported', () => {
    render(<RobotResolver apiClient={{ emergencyResolve: vi.fn() }} onResolved={vi.fn()} onValueChange={vi.fn()} userId={7} value="" />)
    expect(screen.getByLabelText('Номер или VIN робота')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Сканировать' })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 8: Implement the resolver and `/robots?q=` controller**

`RobotResolver` is a controlled form. On submit it calls `parseRobotReference`; invalid input renders “Введите номер, VIN или ссылку на робота” without a request. A valid input calls `apiClient.emergencyResolve(reference)`, stores the successful normalized VIN via `rememberRobot(userId, { query: reference, vin: result.vin })`, and invokes `onResolved`. Use `classifyApiError` for all failure kinds, include request ID, expose retry only when `retryable` is true, and give forbidden/not-found states a safe back action. The scan button is rendered only when `scannerSupported()` and opens `RobotScanner`; detection writes the value and submits through the same resolver path.

`RobotsPage` reads `q` from `useSearchParams`, writes it with replace while input changes, and on success calls:

```typescript
navigate(`/robots/${encodeURIComponent(result.vin)}?park=${parkId}`, { replace: false })
```

Omit `?park` when `parkId` is null. Render recent items as links to `/robots/:vin` with the short query, full VIN, and `Открыт ${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(new Date(item.openedAt))}`; display “Недавние роботы хранятся 30 дней на этом устройстве для текущего пользователя” and a 44px “Очистить” button. Show a neutral `Icon name="robot"` scheme, not a temporary stock/AI photograph.

Use `robots.css` to make the search panel maximum `48rem`, a one-column card list through `599px`, two columns at `600–1199px`, and three columns at `>=1200px`. All controls use 16px mobile text, 44px targets, `min-width: 0`, `overflow-wrap: anywhere`, token surfaces and `2px solid var(--rp-focus)` focus-visible. Hide `.rp-robot-scan-action` at `>=900px`; manual input remains visible at every width.

- [ ] **Step 9: Run Robots resolver GREEN and commit**

```bash
cd apps/web
npx vitest run \
  src/domains/robots/robotReference.test.ts \
  src/domains/robots/recentRobots.test.ts \
  src/domains/robots/RobotScanner.test.tsx \
  src/domains/robots/RobotResolver.test.tsx
npm run build
git diff --check
git diff --name-only
git add \
  src/domains/robots/robotReference.ts \
  src/domains/robots/robotReference.test.ts \
  src/domains/robots/recentRobots.ts \
  src/domains/robots/recentRobots.test.ts \
  src/domains/robots/RobotScanner.tsx \
  src/domains/robots/RobotScanner.test.tsx \
  src/domains/robots/RobotResolver.tsx \
  src/domains/robots/RobotResolver.test.tsx \
  src/domains/robots/RobotsPage.tsx \
  src/domains/robots/robots.css
git commit -m "feat(robots): add scoped resolver recents and scanner"
```

Expected: parsing, retention, camera cleanup and resolver tests pass; build succeeds; legacy recent storage remains untouched.

---

### Task 7: Compose a truthful robot detail card from identity, snapshot and related work

**Files:**

- Create: `apps/web/src/domains/robots/robotDetailData.ts`
- Create: `apps/web/src/domains/robots/robotDetailData.test.ts`
- Create: `apps/web/src/domains/robots/robotDetailModel.ts`
- Create: `apps/web/src/domains/robots/robotDetailModel.test.ts`
- Create: `apps/web/src/domains/robots/RobotIdentityCard.tsx`
- Create: `apps/web/src/domains/robots/RobotDetailView.tsx`
- Create: `apps/web/src/domains/robots/RobotDetailView.test.tsx`
- Create: `apps/web/src/domains/robots/RobotPage.tsx`
- Modify: `apps/web/src/domains/robots/robots.css`

**Interfaces:**

- Produces: `RobotDetailApiClient = Pick<typeof api, 'emergencyResolve' | 'emergencySnapshot' | 'mechanicRobotTickets' | 'operatorRobotTickets' | 'trackerRobotTickets'>`.
- Produces: `resolveRobotDetail(client, reference): Promise<{ vin; sections; snapshot }>` and `loadRelatedRobotWork(client, user, vin): Promise<Blocker[] | null>`; `user.role` remains a raw string and `null` means effective `tracker.read` is absent.
- Produces: `RobotConnectionState = 'device-offline' | 'robot-offline' | 'online' | 'unknown'`; `RobotDetailViewModel`; `buildRobotDetailModel(snapshot, browserOnline, now)`.
- Produces: `RobotDetailView({ snapshot; relatedWork; relatedWorkError; workScopeLabel; parkId; browserOnline; onRetrySnapshot; onRetryWork })`.
- Produces: `RobotPage({ apiClient? })`; it resolves a short-number route parameter, replaces it with the canonical uppercase VIN, and stores the successful recent item for the current user.
- Truthfulness rule: the page says “Область связанных задач: доступные роли очереди Tracker”, not that the selected park is the robot's actual park. It renders no photo URL and no fabricated event rows.

- [ ] **Step 1: Write RED tests for role-aware related-work routing**

Create `robotDetailData.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import { loadRelatedRobotWork, resolveRobotDetail, type RobotDetailApiClient } from './robotDetailData'

function client(): RobotDetailApiClient {
  return {
    emergencyResolve: vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [{ id: 'wheels', title: 'Колёса' }] })),
    emergencySnapshot: vi.fn(async () => ({
      vin: 'YASADR00000000447', short_number: '447', observed_at: '2026-09-02T09:00:00Z', online: true,
      speed: 0, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
      mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true,
      connection: 'lte', error_banner: null, lat: 55, lon: 37, heading_deg: 0, wheels_fault: [],
    })),
    mechanicRobotTickets: vi.fn(async () => ({ query: '447', items: [] })),
    operatorRobotTickets: vi.fn(async () => ({ query: '447', items: [] })),
    trackerRobotTickets: vi.fn(async () => ({ query: '447', items: [] })),
  }
}

describe('robot detail data', () => {
  it('resolves identity before loading its snapshot', async () => {
    const apiClient = client()
    const result = await resolveRobotDetail(apiClient, '447')
    expect(result.vin).toBe('YASADR00000000447')
    expect(apiClient.emergencySnapshot).toHaveBeenCalledWith('YASADR00000000447')
  })

  it.each([
    ['mechanic', 'mechanicRobotTickets'],
    ['operator', 'operatorRobotTickets'],
    ['admin', 'trackerRobotTickets'],
    ['royal', 'trackerRobotTickets'],
    ['field_lead', 'trackerRobotTickets'],
  ] as const)('uses the scoped %s ticket source', async (role, method) => {
    const apiClient = client()
    await loadRelatedRobotWork(
      apiClient,
      { role, permissions: ['tracker.read'] },
      'YASADR00000000447',
    )
    expect(apiClient[method]).toHaveBeenCalledWith('YASADR00000000447')
  })

  it.each(['driver', 'field_lead'])('does not request Tracker data without tracker.read for %s', async (role) => {
    const apiClient = client()
    expect(await loadRelatedRobotWork(
      apiClient,
      { role, permissions: ['nav.robot_search'] },
      'YASADR00000000447',
    )).toBeNull()
    expect(apiClient.mechanicRobotTickets).not.toHaveBeenCalled()
    expect(apiClient.operatorRobotTickets).not.toHaveBeenCalled()
    expect(apiClient.trackerRobotTickets).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run data tests and confirm RED**

```bash
cd apps/web
npx vitest run src/domains/robots/robotDetailData.test.ts
```

Expected: FAIL because `robotDetailData.ts` does not exist.

- [ ] **Step 3: Implement the data adapter without broadening scope**

Create `robotDetailData.ts`:

```typescript
import { api, type Blocker, type EmergencySnapshot, type User } from '../../api'

export type RobotDetailApiClient = Pick<
  typeof api,
  'emergencyResolve' | 'emergencySnapshot' | 'mechanicRobotTickets' | 'operatorRobotTickets' | 'trackerRobotTickets'
>

export async function resolveRobotDetail(client: RobotDetailApiClient, reference: string) {
  const resolved = await client.emergencyResolve(reference)
  const snapshot = await client.emergencySnapshot(resolved.vin)
  return { ...resolved, snapshot }
}

export async function loadRelatedRobotWork(
  client: RobotDetailApiClient,
  user: Pick<User, 'role' | 'permissions'>,
  vin: string,
): Promise<Blocker[] | null> {
  if (!(user.permissions ?? []).includes('tracker.read')) return null
  if (user.role === 'mechanic') return (await client.mechanicRobotTickets(vin)).items
  if (user.role === 'operator') return (await client.operatorRobotTickets(vin)).items
  return (await client.trackerRobotTickets(vin)).items
}
```

Do not add a `park_id` to these calls: the current role endpoints enforce their own permitted queue set, and the response has no truthful robot-to-park field. `RobotPage` passes the complete `{ role: user.role, permissions: user.permissions }` object without casting to the closed compatibility `EmergencyViewerRole`; a capable custom role uses the generic endpoint, while a custom role lacking `tracker.read` performs no Tracker request.

- [ ] **Step 4: Write RED model tests for device versus robot connectivity**

Create `robotDetailModel.test.ts` with the complete snapshot fixture from Step 1:

```typescript
describe('buildRobotDetailModel', () => {
  it('distinguishes browser offline from a robot offline response', () => {
    expect(buildRobotDetailModel(snapshot({ online: true }), false, now).connection).toEqual({
      state: 'device-offline', tone: 'warning', label: 'Нет сети на этом устройстве',
    })
    expect(buildRobotDetailModel(snapshot({ online: false }), true, now).connection).toEqual({
      state: 'robot-offline', tone: 'critical', label: 'Робот не в сети',
    })
  })

  it('uses server observed_at for freshness', () => {
    expect(buildRobotDetailModel(snapshot({ observed_at: '2026-09-02T09:04:50Z' }), true, now).freshness).toBe('live')
    expect(buildRobotDetailModel(snapshot({ observed_at: '2026-09-02T08:55:00Z' }), true, now).freshness).toBe('stale')
  })
})
```

Set `now = new Date('2026-09-02T09:05:00Z')` and define `snapshot(overrides)` in this file with every required `EmergencySnapshot` field.

- [ ] **Step 5: Run the model test and confirm RED, then implement it**

```bash
cd apps/web
npx vitest run src/domains/robots/robotDetailModel.test.ts
```

Expected: FAIL because the model module is absent.

Create `robotDetailModel.ts` with these rules:

```typescript
export function buildRobotDetailModel(
  snapshot: EmergencySnapshot,
  browserOnline: boolean,
  now = new Date(),
): RobotDetailViewModel {
  const age = Math.max(0, now.getTime() - new Date(snapshot.observed_at).getTime())
  const freshness: Freshness = !browserOnline
    ? 'offline'
    : age <= 30_000
      ? 'live'
      : age <= 300_000
        ? 'fresh'
        : 'stale'
  const connection = !browserOnline
    ? { state: 'device-offline' as const, tone: 'warning' as const, label: 'Нет сети на этом устройстве' }
    : snapshot.online === false
      ? { state: 'robot-offline' as const, tone: 'critical' as const, label: 'Робот не в сети' }
      : snapshot.online === true
        ? { state: 'online' as const, tone: 'success' as const, label: 'Робот на связи' }
        : { state: 'unknown' as const, tone: 'neutral' as const, label: 'Связь не определена' }
  return {
    vin: snapshot.vin,
    shortNumber: snapshot.short_number,
    observedAt: snapshot.observed_at,
    freshness,
    connection,
    criticalReason: snapshot.error_banner || (snapshot.wheels_fault.length ? 'Обнаружена неисправность колёс' : null),
  }
}
```

Declare `RobotDetailViewModel` so `connection.tone` is `StatusTone` and `freshness` is Foundation `Freshness`. Do not derive “last contact” from `updated_at` of a Tracker issue.

- [ ] **Step 6: Write a RED presentational detail test**

Create `RobotDetailView.test.tsx`:

```tsx
it('shows identity, scope, freshness, related work and an honest events empty state', () => {
  render(
    <MemoryRouter>
      <RobotDetailView
        browserOnline
        onRetrySnapshot={vi.fn()}
        onRetryWork={vi.fn()}
        parkId={7}
        relatedWork={[{
          key: 'ROBOPARK-42', summary: 'Робот остановился', status: 'Open', status_key: 'open',
          robot: '447', created_at: '2026-09-02T08:00:00Z', hours_created: '1',
          url: 'https://st.yandex-team.ru/ROBOPARK-42', bucket: 'new',
        }]}
        relatedWorkError={null}
        snapshot={snapshot()}
        workScopeLabel="Доступные роли очереди Tracker; выбор парка не определяет фактический парк робота"
      />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
  expect(screen.getByText('YASADR00000000447')).toBeInTheDocument()
  expect(screen.getByText(/выбор парка не определяет фактический парк/i)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
  expect(screen.getByText('История событий пока недоступна')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Начать проверку робота' })).toHaveAttribute('href', '/robots/YASADR00000000447/check?park=7')
})
```

The test file imports `MemoryRouter`, Testing Library and `vi`, then copies the complete typed `snapshot(overrides)` factory from `robotDetailModel.test.ts` so every required `EmergencySnapshot` field, including `observed_at`, is present.

- [ ] **Step 7: Implement the identity card, related-work region and route controller**

`RobotIdentityCard` renders:

- an `Icon name="robot"` inside a neutral `aria-hidden` scheme surface, never `robot-top.png` or an unapproved temporary image;
- visible short number and full VIN with tabular numerals;
- `StatusBadge` connection label and `StaleBadge(state, observedAt)`;
- critical reason as `StatusBadge tone="critical"`, if present;
- one primary link “Начать проверку робота”.

`RobotDetailView` places identity/connection/freshness first, then related tasks, then the explicit unavailable-history state. If `relatedWork === null`, render “Связанные задачи недоступны для роли «Водитель»” rather than an empty list. If `relatedWorkError` exists, show a local retryable `ErrorState` without hiding the robot snapshot. Use `/work/${key}?park=${parkId}` only when `parkId` exists and never call related tasks “текущим заданием”.

`RobotPage` reads `vin` with `useParams`, validates it through `parseRobotReference`, and runs the identity/snapshot resource with `{ persist: false }`. After a successful resolve:

```typescript
const canonicalVin = resolved.vin.toUpperCase()
rememberRobot(user.id, { query: routeReference, vin: canonicalVin })
if (routeReference.toUpperCase() !== canonicalVin) {
  navigate(`/robots/${encodeURIComponent(canonicalVin)}${location.search}`, { replace: true })
}
```

Load related work in a separate resource so its `409`/`502` cannot replace the identity card. Use `useOnlineStatus`; on browser offline keep an in-memory snapshot visible and mark it offline/stale. A top-level resolver `403` explains scope via `classifyApiError`; `404` offers a link back to `/robots`; retries are rendered only for `retryable` failures. Show “Область связанных задач: …” immediately above related tasks and “Выбранный парк: …; он применяется к переходам в Работу” when a park exists.

Extend `robots.css`: identity and work are sequential through `899px`; at `900–1199px` use `minmax(18rem, .8fr) minmax(0, 1.2fr)`; at `>=1200px` use `minmax(22rem, .8fr) minmax(0, 1.2fr)`. Use `--rp-surface`, `--rp-surface-sunken`, `--rp-border`, `--rp-warning-surface`, `--rp-critical-surface` and `--rp-shadow-panel`, never literal colors. At `320px`, VIN wraps, no panel has fixed width, and the primary check action is sticky above bottom navigation.

- [ ] **Step 8: Run robot-detail GREEN and commit**

```bash
cd apps/web
npx vitest run \
  src/domains/robots/robotDetailData.test.ts \
  src/domains/robots/robotDetailModel.test.ts \
  src/domains/robots/RobotDetailView.test.tsx \
  src/domains/robots/recentRobots.test.ts \
  src/domains/robots/robotReference.test.ts
npm run build
git diff --check
git diff --name-only
git add \
  src/domains/robots/robotDetailData.ts \
  src/domains/robots/robotDetailData.test.ts \
  src/domains/robots/robotDetailModel.ts \
  src/domains/robots/robotDetailModel.test.ts \
  src/domains/robots/RobotIdentityCard.tsx \
  src/domains/robots/RobotDetailView.tsx \
  src/domains/robots/RobotDetailView.test.tsx \
  src/domains/robots/RobotPage.tsx \
  src/domains/robots/robots.css
git commit -m "feat(robots): add truthful robot detail workspace"
```

Expected: role routing, connectivity distinctions, truthful labels and canonicalization tests pass; the build succeeds without adding any robot photo asset or history API.

---

### Task 8: Implement the URL-addressed, visibility-aware «Проверка робота» workspace

**Files:**

- Create: `apps/web/src/domains/robots/robotCheckUrl.ts`
- Create: `apps/web/src/domains/robots/robotCheckUrl.test.ts`
- Create: `apps/web/src/domains/robots/polling.ts`
- Create: `apps/web/src/domains/robots/polling.test.ts`
- Create: `apps/web/src/domains/robots/useVisibilityPolling.ts`
- Create: `apps/web/src/domains/robots/useVisibilityPolling.test.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckTabs.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckTabs.test.tsx`
- Create: `apps/web/src/domains/robots/RobotDiagnosticDiagram.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckSummary.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Create: `apps/web/src/domains/robots/RobotCheckPage.tsx`
- Create: `apps/web/src/domains/robots/robot-check.css`
- Modify: `apps/web/src/components/emergency/InspectionMap.tsx`
- Create: `apps/web/src/components/emergency/InspectionMap.test.tsx`

**Interfaces:**

- Produces: `STATIC_CHECK_TABS = [{ id: 'map' }, { id: 'telemetry' }, { id: 'scheme' }]`; `RobotCheckTab`; `checkTabs(sections)`; `parseRobotCheckTab(params, sections)`; `buildRobotCheckSearch(current, tab)`.
- Produces: `POLL_DELAYS_MS = [2500, 5000, 10000, 20000, 30000] as const`; `pollDelayAfterFailure(consecutiveFailures)`.
- Produces: `useVisibilityPolling({ enabled, online, task }): { pending; refreshNow }`; one immediate load, recursive `setTimeout`, pause while hidden/offline, reset after success and bounded backoff after failure.
- Produces: `RobotCheckTabs({ tabs; activeId; onChange })`; automatic activation with roving focus and ArrowLeft/ArrowRight/Home/End.
- Produces: `RobotCheckApiClient = Pick<typeof api, 'emergencySnapshot' | 'emergencySection'>`; `RobotCheckWorkspace({ vin; sections; activeTab; onTabChange; apiClient? })`.
- Produces: `RobotCheckPage({ resolverClient?, checkClient? })`; the route controller resolves/canonicalizes VIN and preserves numeric `park` plus valid `tab`.
- Produces: theme-aware `InspectionMap`; it preserves OpenStreetMap attribution, switches presentation with the resolved Robopark theme, and performs no RAF interpolation or animated pan under `prefers-reduced-motion: reduce`.
- User-facing invariant: no rendered string contains “Emergency”, “Аварийный режим”, cookie names, or raw backend error keys.

- [ ] **Step 1: Write RED tests for canonical tab URL state**

Create `robotCheckUrl.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { buildRobotCheckSearch, checkTabs, parseRobotCheckTab } from './robotCheckUrl'

const sections = [
  { id: 'wheels', title: 'Колёса' },
  { id: 'power', title: 'Питание' },
]

describe('robot check URL', () => {
  it('combines static and dynamic tabs without duplicate reserved ids', () => {
    expect(checkTabs([...sections, { id: 'map', title: 'Другая карта' }]).map((tab) => tab.id)).toEqual([
      'map', 'telemetry', 'scheme', 'wheels', 'power',
    ])
  })

  it('restores only a valid tab and otherwise selects map', () => {
    expect(parseRobotCheckTab(new URLSearchParams('park=7&tab=wheels'), sections)).toBe('wheels')
    expect(parseRobotCheckTab(new URLSearchParams('park=7&tab=secret'), sections)).toBe('map')
  })

  it('preserves park, writes dynamic tabs and omits the default map tab', () => {
    expect(buildRobotCheckSearch(new URLSearchParams('park=7&tab=wheels'), 'telemetry')).toBe('?park=7&tab=telemetry')
    expect(buildRobotCheckSearch(new URLSearchParams('park=7&tab=wheels'), 'map')).toBe('?park=7')
  })
})
```

- [ ] **Step 2: Run URL tests and confirm RED, then implement the codec**

```bash
cd apps/web
npx vitest run src/domains/robots/robotCheckUrl.test.ts
```

Expected: FAIL because `robotCheckUrl.ts` does not exist.

Implement:

```typescript
import type { EmergencySection } from '../../api'

export type RobotCheckTab = { id: string; title: string; kind: 'map' | 'telemetry' | 'scheme' | 'section' }
export const STATIC_CHECK_TABS: readonly RobotCheckTab[] = [
  { id: 'map', title: 'Карта', kind: 'map' },
  { id: 'telemetry', title: 'Телеметрия', kind: 'telemetry' },
  { id: 'scheme', title: 'Схема', kind: 'scheme' },
]
const RESERVED = new Set(STATIC_CHECK_TABS.map((tab) => tab.id))

export function checkTabs(sections: EmergencySection[]): RobotCheckTab[] {
  return [
    ...STATIC_CHECK_TABS,
    ...sections
      .filter((section) => !RESERVED.has(section.id))
      .map((section) => ({ ...section, kind: 'section' as const })),
  ]
}

export function parseRobotCheckTab(params: URLSearchParams, sections: EmergencySection[]): string {
  const requested = params.get('tab')?.trim() || 'map'
  return checkTabs(sections).some((tab) => tab.id === requested) ? requested : 'map'
}

export function buildRobotCheckSearch(current: URLSearchParams, tab: string): string {
  const next = new URLSearchParams()
  const park = current.get('park')
  if (park && /^\d+$/.test(park)) next.set('park', park)
  if (tab !== 'map') next.set('tab', tab)
  const query = next.toString()
  return query ? `?${query}` : ''
}
```

- [ ] **Step 3: Write RED scheduler and hook tests**

Create `polling.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { pollDelayAfterFailure } from './polling'

it('uses the bounded 2.5–30 second backoff sequence', () => {
  expect([0, 1, 2, 3, 4, 5, 99].map(pollDelayAfterFailure)).toEqual([
    2500, 5000, 10000, 20000, 30000, 30000, 30000,
  ])
})
```

Create `useVisibilityPolling.test.tsx`:

```tsx
import { act, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useVisibilityPolling } from './useVisibilityPolling'

function Probe({ task, online = true }: { task: () => Promise<void>; online?: boolean }) {
  useVisibilityPolling({ enabled: true, online, task })
  return null
}

describe('useVisibilityPolling', () => {
  afterEach(() => {
    vi.useRealTimers()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  })

  it('backs off failures, pauses hidden and refreshes when visible again', async () => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    const task = vi.fn()
      .mockRejectedValueOnce(new Error('one'))
      .mockRejectedValueOnce(new Error('two'))
      .mockResolvedValue(undefined)
    render(<Probe task={task} />)
    await act(async () => undefined)
    expect(task).toHaveBeenCalledTimes(1)

    await act(async () => vi.advanceTimersByTimeAsync(2_500))
    expect(task).toHaveBeenCalledTimes(2)
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    await act(async () => vi.advanceTimersByTimeAsync(30_000))
    expect(task).toHaveBeenCalledTimes(2)

    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(task).toHaveBeenCalledTimes(3)
    await act(async () => vi.advanceTimersByTimeAsync(2_500))
    expect(task).toHaveBeenCalledTimes(4)
  })
})
```

- [ ] **Step 4: Run polling tests and confirm RED, then implement recursive scheduling**

```bash
cd apps/web
npx vitest run \
  src/domains/robots/polling.test.ts \
  src/domains/robots/useVisibilityPolling.test.tsx
```

Expected: FAIL because the scheduler and hook modules do not exist.

Create `polling.ts`:

```typescript
export const POLL_DELAYS_MS = [2_500, 5_000, 10_000, 20_000, 30_000] as const
export function pollDelayAfterFailure(consecutiveFailures: number): number {
  return POLL_DELAYS_MS[Math.min(Math.max(0, consecutiveFailures), POLL_DELAYS_MS.length - 1)]
}
```

Implement `useVisibilityPolling` with refs for the latest task, timer, in-flight promise, failure count and mounted flag. `run()` coalesces concurrent manual/timer calls; success resets failures and schedules `2500`, failure schedules `pollDelayAfterFailure(failures)` then increments failures. `schedule()` refuses when disabled, `!online`, hidden or unmounted. A `visibilitychange` to visible runs immediately; hidden clears the timeout. A change from `online=false` to `true` runs immediately. Cleanup removes the listener and clears the timeout. The source file must contain `window.setTimeout` and must not contain `setInterval`.

- [ ] **Step 5: Write RED keyboard tests for the tab strip**

Create `RobotCheckTabs.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RobotCheckTabs } from './RobotCheckTabs'

const tabs = [
  { id: 'map', title: 'Карта', kind: 'map' as const },
  { id: 'telemetry', title: 'Телеметрия', kind: 'telemetry' as const },
  { id: 'scheme', title: 'Схема', kind: 'scheme' as const },
]

it('uses roving focus and automatic keyboard activation', () => {
  const onChange = vi.fn()
  render(<RobotCheckTabs activeId="map" onChange={onChange} tabs={tabs} />)
  const map = screen.getByRole('tab', { name: 'Карта' })
  const telemetry = screen.getByRole('tab', { name: 'Телеметрия' })
  map.focus()
  fireEvent.keyDown(map, { key: 'ArrowRight' })
  expect(telemetry).toHaveFocus()
  expect(onChange).toHaveBeenCalledWith('telemetry')
  fireEvent.keyDown(telemetry, { key: 'End' })
  expect(screen.getByRole('tab', { name: 'Схема' })).toHaveFocus()
  expect(screen.getByRole('tablist', { name: 'Разделы проверки робота' })).toBeInTheDocument()
})
```

- [ ] **Step 6: Run tab test and confirm RED, then implement WAI-ARIA tabs**

```bash
cd apps/web
npx vitest run src/domains/robots/RobotCheckTabs.test.tsx
```

Expected: FAIL because `RobotCheckTabs.tsx` does not exist.

Each tab button has `id="robot-check-tab-${id}"`, `aria-controls="robot-check-panel-${id}"`, `aria-selected`, `tabIndex={active ? 0 : -1}` and a ref. ArrowRight/Left wrap, Home selects index `0`, End selects `tabs.length - 1`; the handler focuses the destination and invokes `onChange(destination.id)`. `RobotCheckWorkspace` owns one `role="tabpanel"` with inverse `aria-labelledby` and matching ID.

- [ ] **Step 7: Write a RED workspace test for first-level state, dynamic data and manual refresh**

Create `RobotCheckWorkspace.test.tsx`:

```tsx
it('shows the first-level decision data and fetches only the active dynamic section', async () => {
  const apiClient = checkClient()
  const onTabChange = vi.fn()
  render(
    <RobotCheckWorkspace
      activeTab="wheels"
      apiClient={apiClient}
      onTabChange={onTabChange}
      sections={[{ id: 'wheels', title: 'Колёса' }]}
      vin="YASADR00000000447"
    />,
  )
  expect(await screen.findByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
  expect(screen.getByText('Робот на связи')).toBeInTheDocument()
  expect(screen.getByText('Данные на 2 сентября 2026')).toBeInTheDocument()
  expect(apiClient.emergencySection).toHaveBeenCalledWith('YASADR00000000447', 'wheels')
  expect(screen.getByRole('button', { name: 'Обновить данные' })).toBeInTheDocument()
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Колёса')
})

it('labels browser offline separately while retaining the last snapshot', async () => {
  setNavigatorOnline(false)
  render(<RobotCheckWorkspace activeTab="map" apiClient={checkClient()} onTabChange={vi.fn()} sections={[]} vin="YASADR00000000447" />)
  expect(await screen.findByText('Нет сети на этом устройстве')).toBeInTheDocument()
  expect(screen.queryByText('Робот не в сети')).not.toBeInTheDocument()
})
```

In the same test file, define a full `EmergencySnapshot` fixture with `observed_at`, `checkClient()` returning `emergencySnapshot` plus `emergencySection`, and `setNavigatorOnline(value)` using `Object.defineProperty`. Freeze time at `2026-09-02T09:05:00Z` and restore timers/connectivity in `afterEach`.

Create `InspectionMap.test.tsx` with a deterministic Leaflet mock and a mutable `matchMedia` stub. In dark resolved theme, assert the map root exposes `data-map-theme="dark"`; switch ThemeProvider to light and assert the same mounted map becomes light without creating a second Leaflet instance. With reduced motion enabled, change coordinates and assert the marker receives its final latitude/longitude synchronously, `requestAnimationFrame` is never called, and follow-pan uses `{ animate: false }`. With motion allowed, retain one interpolation assertion. In every case assert the tile-layer attribution still contains `OpenStreetMap`.

- [ ] **Step 8: Implement the two-level workspace and original neutral diagram**

`RobotCheckWorkspace` keeps `snapshot`, `snapshotError`, a `Record<sectionId, EmergencySectionDetail>`, and per-section errors in component state; it does not persist telemetry to localStorage. Its polling task uses `Promise.allSettled` to request `emergencySnapshot(vin)` plus `emergencySection(vin, activeTab)` only when `activeTab` is dynamic. Apply each fulfilled value even if the other request fails, then throw the first rejection so backoff advances. Manual refresh calls `refreshNow` and remains enabled even while `navigator.onLine === false`.

The first-level `RobotCheckSummary` always precedes tabs and renders:

1. `Робот <short_number>` and full VIN;
2. browser/robot connection label from Task 7;
3. `StaleBadge` from `observed_at` plus a visible formatted timestamp;
4. location text `lat, lon` or “Координаты не получены”;
5. error banner/wheel fault reason, if present;
6. exactly one primary `Button`: “Повторить проверку” for device/robot offline or critical state, otherwise “Обновить данные”.

Update the retained `InspectionMap` as part of the same task. Read `resolvedTheme` from Foundation `useTheme()` and put `data-map-theme={resolvedTheme}` on the map root; `robot-check.css` applies a restrained filter to the Leaflet tile pane in dark mode while controls/markers remain unfiltered and visible. Subscribe to `(prefers-reduced-motion: reduce)` with cleanup. When reduced, cancel any active RAF, move the marker directly to the final coordinate, and call follow-pan with `animate: false`; when allowed, keep the existing bounded interpolation and pan duration. Theme/motion changes must not recreate the map or drop user-pan state. Do not change tile provider, attribution, or add an API key.

Static tab panels are exact:

- `map`: `InspectionMap` when both coordinates exist, otherwise `EmptyState` “Координаты не получены”; keep follow/pan behavior subject to the reduced-motion contract above.
- `telemetry`: labelled values for speed, charge, both batteries, disk, mode, ICP, LTE and connection; missing values display “Нет данных”.
- `scheme`: `RobotDiagnosticDiagram` with code-native `<svg>` body and absolutely positioned 44px wheel buttons generated from existing `WHEEL_HOTSPOTS`; token fills/strokes, text wheel labels and `onSelectWheels` opening a dynamic wheels tab when available.
- dynamic section: field labels plus `<pre className="rp-check-field-lines">` using `white-space: pre-wrap; overflow-wrap: anywhere`; empty, loading and local retry states remain inside the panel.

Do not import `robot-top.png` or `RobotSchematic`. The diagram silhouette is this original neutral geometry:

```tsx
<svg aria-hidden="true" viewBox="0 0 240 320">
  <rect className="rp-check-diagram-body" height="220" rx="36" width="160" x="40" y="50" />
  <rect className="rp-check-diagram-lid" height="54" rx="18" width="112" x="64" y="24" />
  <circle className="rp-check-diagram-sensor" cx="120" cy="51" r="10" />
  <path className="rp-check-diagram-divider" d="M56 160h128M120 78v176" />
</svg>
```

When an old snapshot remains after a failed poll, show it with `StaleBadge state="stale"` and a non-blocking warning. If there is no snapshot, use `LoadingState` or `ErrorState`. Map `emergency_cookie_invalid` to “Интеграция проверки робота требует внимания”; expose `Открыть настройки` at `/admin/robot-check` only when `canAccessRoute(user, 'admin-robot-check')` is true (including a capable custom role), otherwise render text-only administrator guidance. Never display the raw key.

- [ ] **Step 9: Implement `RobotCheckPage` canonicalization and route state**

Resolve the route parameter through `emergencyResolve`. While resolving, show a page loading state. Classify by detail before status: `emergency_cookie_invalid` is the configuration state “Интеграция проверки робота требует внимания”, even though its HTTP status is `403`; only a remaining `forbidden` error is a scope explanation. `404` offers `/robots`; retryable failures expose retry/request ID. Add equivalent tests to `RobotCheckWorkspace.test.tsx` and `LegacyEmergencyRedirect.test.tsx`, including an operator who sees contact guidance without an inaccessible admin link and an authorized settings actor who sees `Открыть настройки`. Once resolved:

```typescript
const activeTab = parseRobotCheckTab(searchParams, resolved.sections)
const writeTab = (tab: string) => {
  navigate(
    `/robots/${encodeURIComponent(resolved.vin)}/check${buildRobotCheckSearch(searchParams, tab)}`,
    { replace: true },
  )
}
```

If the route reference is not the resolved uppercase VIN, replace it without changing the parsed tab or numeric park. If the requested tab is invalid, replace the URL with `map`. Call `rememberRobot(user.id, { query: routeReference, vin: resolved.vin })` only after successful resolution. Page title is “Проверка робота”; include back links to `/robots/:vin` and `/robots`, and no technical API name in copy.

`robot-check.css` uses a calm single column through `899px`, summary plus panel split at `900–1199px`, and max-width content with a stable two-column detail at `>=1200px`. The tab strip may scroll internally at 320px, but `body`, shell and panel must not; tabs remain 44px high. Use only Foundation tokens including `--rp-critical-surface`, `--rp-warning-surface`, `--rp-success-surface`, `--rp-info-surface`, `--rp-overlay`, and `--rp-shadow-panel`. Do not write raw color literals or theme-specific selectors. Disable map/diagram transitions under reduced motion.

- [ ] **Step 10: Run Robot Check GREEN, assert no interval/legacy copy, and commit**

```bash
cd apps/web
npx vitest run \
  src/domains/robots/robotCheckUrl.test.ts \
  src/domains/robots/polling.test.ts \
  src/domains/robots/useVisibilityPolling.test.tsx \
  src/domains/robots/RobotCheckTabs.test.tsx \
  src/domains/robots/RobotCheckWorkspace.test.tsx \
  src/domains/robots/robotDetailModel.test.ts \
  src/components/emergency/InspectionMap.test.tsx
! rg -n 'Аварийный режим|\bEmergency\b' src/domains/robots --glob '*.tsx'
npm run build
git diff --check
git diff --name-only
git add \
  src/domains/robots/robotCheckUrl.ts \
  src/domains/robots/robotCheckUrl.test.ts \
  src/domains/robots/polling.ts \
  src/domains/robots/polling.test.ts \
  src/domains/robots/useVisibilityPolling.ts \
  src/domains/robots/useVisibilityPolling.test.tsx \
  src/domains/robots/RobotCheckTabs.tsx \
  src/domains/robots/RobotCheckTabs.test.tsx \
  src/domains/robots/RobotDiagnosticDiagram.tsx \
  src/domains/robots/RobotCheckSummary.tsx \
  src/domains/robots/RobotCheckWorkspace.tsx \
  src/domains/robots/RobotCheckWorkspace.test.tsx \
  src/domains/robots/RobotCheckPage.tsx \
  src/domains/robots/robot-check.css \
  src/components/emergency/InspectionMap.tsx \
  src/components/emergency/InspectionMap.test.tsx
git commit -m "feat(robot-check): add accessible visibility-aware diagnostics"
```

Expected: URL, backoff, pause/resume, keyboard, device-offline and workspace tests pass; static scan finds neither intervals nor legacy product copy; build succeeds.

---

### Task 9: Register canonical operational routes and lossless legacy redirects

**Files:**

- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/AppRouter.test.tsx`
- Create: `apps/web/src/app/routing/LegacyEmergencyRedirect.tsx`
- Create: `apps/web/src/app/routing/LegacyEmergencyRedirect.test.tsx`
- Modify: `apps/web/src/components/tracker/robotHealth.ts`
- Modify: `apps/web/src/components/tracker/robotHealth.test.ts`
- Modify: `apps/web/src/components/tracker/RobotCheckPanel.tsx`
- Modify: `apps/web/src/components/tracker/RobotCheckPanel.test.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/src/i18n/errors.test.ts`

**Interfaces:**

- Extends Foundation `AppRouteId` with `'work-issue' | 'robot-detail' | 'legacy-robot-check'`.
- Rebinds existing IDs: `overview → OverviewPage`, `work → WorkPage`, `robots → RobotsPage`, `robot-check → RobotCheckPage`.
- Adds registry entries for `work-issue`, `robot-detail`, and `legacy-robot-check`, preserving the exhaustive `Record<AppRouteId, ReactElement>` check.
- Produces: `LegacyRobotCheckApiClient = Pick<typeof api, 'emergencyResolve'>`; `LegacyEmergencyRedirect({ apiClient? })`.
- Redirect contract: `/dashboard → /overview`, `/tasks → /work`, `/robots/search → /robots`; `/emergency?q|robot=:value&tab=:tabId&park=:parkId → /robots/:resolvedVin/check?park=:parkId&tab=:tabId`; no robot goes to `/robots`.

- [ ] **Step 1: Write RED route-manifest and access assertions**

Extend `routeManifest.test.ts`:

```typescript
it('declares every Phase 2 canonical and compatibility route exactly once', () => {
  expect(ROUTE_MANIFEST.filter((route) => route.path === '/overview')).toHaveLength(1)
  expect(ROUTE_MANIFEST.find((route) => route.id === 'overview')?.legacyPaths).toContain('/dashboard')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'work')?.legacyPaths).toContain('/tasks')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'work-issue')?.path).toBe('/work/:issueKey')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'robots')?.legacyPaths).toContain('/robots/search')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'robot-detail')?.path).toBe('/robots/:vin')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'robot-check')?.path).toBe('/robots/:vin/check')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'legacy-robot-check')?.path).toBe('/emergency')
  expect(ROUTE_MANIFEST.find((route) => route.id === 'robot-check')?.nav).toBeUndefined()
})
```

Extend `accessPolicy.test.ts` with approved driver access to `overview`, `robots`, `robot-detail`, `robot-check`, and `legacy-robot-check`, plus denial for `work`/`work-issue`. In the Foundation landing table, replace—not duplicate—the transitional driver row `{ permissions: ['nav.emergency'], expected: '/emergency' }` with `{ permissions: ['nav.dashboard', 'nav.robot_search', 'nav.emergency'], expected: '/overview' }`; after this task no landing assertion may expect `/emergency`. Assert an operator with `nav.tasks` can access both Work IDs and a user without `nav.emergency` cannot access either check route. Because `robot-check` becomes parameterized, add these landing invariants:

First extend the Foundation test's independent `protectedRoutes` expectation table; do not read these values back from `ROUTE_MANIFEST`:

```ts
// Insert these rows into the existing literal before its `] as const`.
{ id: 'work-issue', permission: 'nav.tasks', operatorOnly: false, mechanicPark: true },
{ id: 'robot-detail', permission: 'nav.robot_search', operatorOnly: false, mechanicPark: true },
{ id: 'legacy-robot-check', permission: 'nav.emergency', operatorOnly: false, mechanicPark: true },
```

Keep the existing Cartesian loops over every system/custom role, access status, permission-present state, forced-password state, and mechanic park prerequisite. Thus these three rows receive exactly the same denial coverage as top-level destinations; the selected happy/deny assertions below supplement rather than replace the cumulative matrix.

```ts
const driver = testUser({
  role: 'driver',
  access_status: 'approved',
  permissions: ['nav.dashboard', 'nav.robot_search', 'nav.emergency'],
})
expect(landingPathForUser(driver)).toBe('/overview')

for (const role of ['mechanic', 'operator', 'driver', 'admin', 'royal', 'field_lead']) {
  const landing = landingPathForUser(testUser({
    role,
    access_status: 'approved',
    permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency'],
    parks: [northPark],
  }))
  expect(landing).not.toContain(':')
}
```

- [ ] **Step 2: Run routing tests and confirm RED**

```bash
cd apps/web
npx vitest run \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts
```

Expected: FAIL because detail/legacy IDs and canonical parameterized routes are absent.

- [ ] **Step 3: Extend the manifest without duplicating navigation entries**

Add the three IDs to the union, change the existing four entries and insert these non-navigation records:

```typescript
{
  id: 'work-issue', path: '/work/:issueKey', label: 'Работа', icon: 'work',
  permission: 'nav.tasks', prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
},
{
  id: 'robot-detail', path: '/robots/:vin', label: 'Робот', icon: 'robot',
  permission: 'nav.robot_search', prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
},
{
  id: 'robot-check', path: '/robots/:vin/check', label: 'Проверка робота', icon: 'robot-check',
  permission: 'nav.emergency', prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
},
{
  id: 'legacy-robot-check', path: '/emergency', label: 'Проверка робота', icon: 'robot-check',
  permission: 'nav.emergency', prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
},
```

Keep `nav` only on `/overview`, `/work`, and `/robots`; `/robot-check` is reached from robot search/card/task, never as a parameterized navigation link. Existing canonical entries use labels `Обзор`, `Работа`, `Роботы`, icons `overview`, `work`, `robot`, and legacy paths listed above. Remove `/emergency` from any `legacyPaths` array because it needs an asynchronous resolver.

In the same atomic access-policy change, replace the Foundation driver preference that pointed to `/emergency` with `['overview', 'robots']`. Landing candidates must be accessible manifest records with `nav` metadata and a static path (`!route.path.includes(':')`); never return a detail/check template containing `:issueKey` or `:vin`. A driver without `nav.dashboard` but with `nav.robot_search` lands on `/robots`; one with neither static permission follows the ordinary `/no-cabinet` fallback.

- [ ] **Step 4: Write RED legacy redirect tests**

Create `LegacyEmergencyRedirect.test.tsx` with `MemoryRouter`, a `/emergency` route, destination routes and a location probe:

```tsx
it('resolves q and preserves the selected park and tab', async () => {
  const emergencyResolve = vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [{ id: 'wheels', title: 'Колёса' }] }))
  renderLegacy('/emergency?q=447&tab=wheels&park=7', { emergencyResolve })
  expect(await screen.findByTestId('location')).toHaveTextContent(
    '/robots/YASADR00000000447/check?park=7&tab=wheels',
  )
  expect(emergencyResolve).toHaveBeenCalledWith('447')
})

it('supports the old robot key', async () => {
  renderLegacy('/emergency?robot=447', { emergencyResolve: vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [] })) })
  expect(await screen.findByTestId('location')).toHaveTextContent('/robots/YASADR00000000447/check')
})

it('sends an empty legacy route to robot search with its park', async () => {
  renderLegacy('/emergency?park=7', { emergencyResolve: vi.fn() })
  expect(await screen.findByTestId('location')).toHaveTextContent('/robots?park=7')
})
```

Implement `renderLegacy` in that file by rendering `Routes` for `/emergency`, `/robots`, and `/robots/:vin/check`, with `LegacyEmergencyRedirect apiClient={client}` and `LocationProbe` returning `pathname + search`.

- [ ] **Step 5: Implement the compatibility controller and registry**

`LegacyEmergencyRedirect` reads `q ?? robot`, accepts only numeric `park`, and preserves a trimmed `tab`. With no robot it returns `<Navigate replace to={`/robots${parkSearch}`} />`. Otherwise it calls `emergencyResolve`, shows `LoadingState`, and after success returns `Navigate` to the encoded uppercase VIN with search ordered `park`, then `tab`. A failure is mapped through `classifyApiError`, includes retry only when `retryable`, and always offers a secondary `/robots` link; it must never mount the legacy `EmergencyViewer`.

Update `ROUTE_ELEMENTS` exactly:

```tsx
overview: <OverviewPage />,
work: <WorkPage />,
'work-issue': <WorkPage />,
robots: <RobotsPage />,
'robot-detail': <RobotPage />,
'robot-check': <RobotCheckPage />,
'legacy-robot-check': <LegacyEmergencyRedirect />,
```

Retain all Foundation/non-operational registry entries unchanged. Add AppRouter tests for `/dashboard?park=7`, `/tasks?park=7&status=open`, and `/robots/search?q=447&park=7`, asserting canonical pathname plus preserved search. Test `/work/ROBOPARK-42?park=7&status=open` and `/robots/YASADR00000000447/check?park=7&tab=map` direct loads through the route gate.

- [ ] **Step 6: Migrate canonical task links and operational copy**

Rename `emergencyPathForRobot` to `robotCheckPathForRobot` in `robotHealth.ts` and return:

```typescript
export function robotCheckPathForRobot(robot: string): string {
  return `/robots/${encodeURIComponent(robot.trim())}/check`
}
```

Update `RobotCheckPanel` and its tests to use accessible copy such as “Проверить робота 447” and the canonical path; Task 8 canonicalizes a short number after resolution. In `ru.ts`, update operational values to `Обзор`, `Работа`, `Роботы`, `Проверка робота`, “Проверяем критические состояния…”, “Критических состояний не обнаружено”, and “Не удалось получить данные проверки робота”. Technical object keys and API method names remain unchanged. Update `errors.test.ts` so rendered error copy contains no standalone product name `Emergency`.

- [ ] **Step 7: Run route/copy GREEN and commit**

```bash
cd apps/web
npx vitest run \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts \
  src/app/routing/AppRouter.test.tsx \
  src/app/routing/LegacyEmergencyRedirect.test.tsx \
  src/components/tracker/robotHealth.test.ts \
  src/components/tracker/RobotCheckPanel.test.tsx \
  src/i18n/errors.test.ts
npm run check-nav
npm run build
git diff --check
git diff --name-only
git add \
  src/app/routing/routeManifest.ts \
  src/app/routing/routeManifest.test.ts \
  src/app/routing/accessPolicy.test.ts \
  src/app/routing/AppRouter.tsx \
  src/app/routing/AppRouter.test.tsx \
  src/app/routing/LegacyEmergencyRedirect.tsx \
  src/app/routing/LegacyEmergencyRedirect.test.tsx \
  src/components/tracker/robotHealth.ts \
  src/components/tracker/robotHealth.test.ts \
  src/components/tracker/RobotCheckPanel.tsx \
  src/components/tracker/RobotCheckPanel.test.tsx \
  src/i18n/ru.ts \
  src/i18n/errors.test.ts
git commit -m "feat(routing): switch operations to canonical routes"
```

Expected: canonical routes, direct links and lossless legacy redirects pass; exhaustive route registry builds; operational copy uses only «Проверка робота».

---

### Task 10: Lock role journeys, responsive behavior, themes and accessibility in deterministic E2E

**Files:**

- Create: `apps/web/e2e/operational/fixtures.ts`
- Create: `apps/web/e2e/operational/overview.spec.ts`
- Create: `apps/web/e2e/operational/work.spec.ts`
- Create: `apps/web/e2e/operational/robots.spec.ts`
- Create: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Create via Playwright update command under: `apps/web/e2e/operational/responsive-visual.spec.ts-snapshots/`

**Interfaces:**

- Consumes unchanged: Foundation `installMockApi(page, { user, parks, routes })`, `MockRoute`, and `assertNoSeriousA11yViolations(page)`.
- Produces deterministic fixtures for all five roles, two parks, dashboard summary, Tracker pages/details/actions, resolve/snapshot/sections; every time value is fixed at `2026-09-02T09:00:00Z`.
- E2E must not call a live API, database, Tracker, map data source, camera, or credentials. Custom API routes are all `/api/...` and precede Foundation defaults.
- Visual matrix: widths `320`, `390`, `768`, `1024`, `1440`; height `900`; light and dark themes; stable animations disabled.

- [ ] **Step 1: Create complete deterministic operational fixtures**

In `fixtures.ts`, export `parkNorth`, `parkSouth`, `userForRole(role)`, `summaryForPark(parkId)`, `snapshot`, `issue`, and `operationalRoutes(options?)`. Use Foundation `MockRoute[]`; match parameterized paths with regex. Mutable action state is captured in the returned closure, not globals. Required routes:

```typescript
{ method: 'GET', path: '/api/dashboard/summary', handler: (request) => ({ json: summaryForPark(Number(new URL(request.url).searchParams.get('park_id'))) }) },
{ method: 'GET', path: '/api/tracker/issues', handler: () => ({ json: { items: [issue], total: 51, limit: 50, offset: 0, has_more: true } }) },
{ method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-42$/, handler: () => ({ json: issue }) },
{ method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/comments', handler: () => ({ json: [] }) },
{ method: 'GET', path: '/api/tracker/transitions/ROBOPARK-42', handler: () => ({ json: [{ id: 'resolve', display: 'Решить' }] }) },
{ method: 'POST', path: '/api/emergency/resolve', handler: () => ({ json: { vin: snapshot.vin, sections: [{ id: 'wheels', title: 'Колёса' }] } }) },
{ method: 'GET', path: `/api/emergency/${snapshot.vin}/snapshot`, handler: () => ({ json: snapshot }) },
{ method: 'GET', path: `/api/emergency/${snapshot.vin}/sections/wheels`, handler: () => ({ json: { id: 'wheels', title: 'Колёса', fields: [{ label: 'Переднее левое', lines: ['Исправно'] }] } }) },
```

Also provide mechanic/operator task endpoints and all six Tracker action routes used by component journeys. Use `status: 204` only for logout-like responses; action handlers return complete `TrackerActionResult` JSON. Block external tile hosts in robot tests with `page.route(/^https:\/\/(?!localhost|127\.0\.0\.1)/, route => route.abort())` so no nondeterministic network enters screenshots.

- [ ] **Step 2: Write role/Overview E2E and confirm RED**

`overview.spec.ts` loops mechanic, operator, driver, admin, royal. For each, install fixtures, open `/overview?park=7`, assert its role-specific heading/primary action and call `assertNoSeriousA11yViolations`. Additionally assert the primary operation is reachable in no more than two clicks: mechanic/operator opens Work detail, driver opens Robots then resolves a robot, admin opens the existing administration entry, royal opens the highest-risk park/work link. Confirm driver never requests `/api/tracker/*` by recording matching requests and expecting an empty array.

Run:

```bash
cd apps/web
npx playwright test e2e/operational/overview.spec.ts --project=chromium
```

Expected: PASS after Tasks 1–9; any failure identifies an operational acceptance regression and is fixed at its source without weakening the assertions.

- [ ] **Step 3: Write Work deep-link, state and action E2E**

`work.spec.ts` must prove:

1. `/work?park=7&status=open&sort=newest&page=2` restores every filter and requests offset `50`;
2. opening ROBOPARK-42 changes the URL to `/work/ROBOPARK-42` while retaining park/filter/sort/page;
3. reloading the detail restores it;
4. back on a `390px` viewport returns to the saved scroll position;
5. a capability-disabled attachment-only issue renders only attachment;
6. close requires `ConfirmDialog`, failure stays open with alert, success closes and refreshes local resources;
7. empty `200`, queue-not-configured `409`, `403`, `404`, `409` mutation, `502`, and gateway-timeout `504` each render their distinct recovery text/request ID.

Use custom route handler closures to inspect query parameters and alternate failure/success responses. Run the spec and correct only Phase 2 behavior uncovered by failing assertions.

- [ ] **Step 4: Write Robots/check/legacy E2E**

`robots.spec.ts` must prove manual number, VIN and pasted legacy deep link resolution; v2 recents isolated between user IDs and clearable; `/robots/447` canonicalizes to full VIN; driver detail makes no Tracker request; `/robots/:vin/check?tab=wheels&park=7` restores the dynamic tab; Arrow keys move/activate tabs; manual refresh is available; robot-offline and browser-offline messages differ; `/emergency?q=447&tab=wheels&park=7` lands on the same canonical check URL. Stub `navigator.mediaDevices`/`BarcodeDetector` only in the scanner-specific test and assert the fake track's `stop` flag after cancel.

Run:

```bash
cd apps/web
npx playwright test \
  e2e/operational/work.spec.ts \
  e2e/operational/robots.spec.ts \
  --project=chromium
```

Expected: Work and Robots journeys pass only against `installMockApi`; no request reaches live data.

- [ ] **Step 5: Add the responsive/theme visual matrix**

In `responsive-visual.spec.ts`, for each width and each theme preseed `robopark-theme`, install deterministic mocks, disable animations/caret with Playwright screenshot options, and snapshot exactly four canonical states: Overview; Work with ROBOPARK-42 open (list plus detail at split widths, detail-only at sequential widths); Robots search; Robot Check. For every page assert:

```typescript
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
expect(overflow).toBeLessThanOrEqual(0)
await assertNoSeriousA11yViolations(page)
```

At widths `320` and `390`, inspect bounding boxes for every visible `button`, `a`, `input:not([type="checkbox"]):not([type="radio"])`, `select`, and `textarea`, asserting width and height are at least `44`; for a checkbox/radio inspect its associated `label` box instead. On every viewport assert computed font size is at least `14px` for visible text-bearing content and controls; at widths through `899`, assert `input`, `select`, and `textarea` are at least `16px`. At `1024` and `1440`, assert Overview state+queue and Work list+detail are simultaneously visible. At `768`, assert sequential Work detail. Add a `1440px` non-snapshot reflow test that sets the root font size to `200%`, then verifies the scope, risk, primary action, and active Work detail remain reachable without document-level horizontal overflow. For system theme, add one non-snapshot test using `page.emulateMedia({ colorScheme: 'dark' })`, stored `system`, reload, and assert `document.documentElement.dataset.theme === 'dark'` without URL/state loss.

Generate reviewed baselines once:

```bash
cd apps/web
npm run test:e2e:update:linux -- e2e/operational/responsive-visual.spec.ts --project=chromium
npm run test:e2e:linux -- e2e/operational/responsive-visual.spec.ts --project=chromium
```

Expected: 40 page/viewport/theme snapshots are stable, no page overflows at 320px, compact targets meet 44px, split content appears only at the approved ranges, and axe reports no applicable WCAG A/AA violations.

- [ ] **Step 6: Run complete phase gates and fix regressions at their source**

```bash
cd apps/api
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q
cd ../web
npm run lint
npm run build
npm run test
npm run check-nav
npm run test:e2e:linux
cd ../..
./scripts/verify.sh
git diff --check
git status --short
```

Expected: Ruff, complete API pytest, web lint/build/Vitest/nav parity, all Playwright projects and repository verification pass. `git status --short` lists only the five operational E2E files and their approved snapshot baselines for this task.

- [ ] **Step 7: Commit E2E evidence**

```bash
git add \
  apps/web/e2e/operational/fixtures.ts \
  apps/web/e2e/operational/overview.spec.ts \
  apps/web/e2e/operational/work.spec.ts \
  apps/web/e2e/operational/robots.spec.ts \
  apps/web/e2e/operational/responsive-visual.spec.ts \
  apps/web/e2e/operational/responsive-visual.spec.ts-snapshots
git commit -m "test(operations): lock role responsive and a11y journeys"
git status --short --branch
```

Expected: commit succeeds and the branch is clean. Phase 2 is complete only if direct canonical routes, compatibility redirects, five role journeys, both themes, all five widths, keyboard tabs and destructive confirmation remain green; Work cache is user-scoped, survives only transient offline/timeout/server revalidation, and is synchronously suppressed and purged for the denied user after `401`/`403`; login, refresh-401 and logout clear resource cache plus every report-draft/recent-robots-v2 browser namespace before another account can render, including delete/recreate with the same numeric ID, while successful reload and transient offline retain same-session storage; and capable custom roles can load scoped related robot work without casts to a closed system-role union.
