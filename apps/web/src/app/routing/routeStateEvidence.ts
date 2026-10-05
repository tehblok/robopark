import { ROUTE_COVERAGE_MANIFEST, type CoverageAudience, type NestedStateKind } from './routeCoverageManifest'
import type { AppRouteId } from './routeManifest'

export type RouteStateEvidence = {
  caseId: string
  routeId: AppRouteId
  stateId: string
  kind: NestedStateKind
  auth: 'authenticated' | 'unauthenticated'
  actorRole: CoverageAudience
  fixture: 'loaded' | 'loading' | 'empty' | 'error' | 'stale' | 'denied' | 'owner-test' | 'not-applicable'
  selector: string
  trigger?: { role: 'button' | 'tab' | 'link'; name: string }
  assertion: { description: string; kind: 'visible' | 'url' }
  notApplicableReason?: string
  ownerTest?: { path: string; title: string; stateKey: string }
  ownerContract?: string
  ownerDriver?: OwnerStateDriver
}

export type OwnerStateDriver = {
  stateKey: string
  stateKind: NestedStateKind
  action: 'assert' | 'tab' | 'button' | 'form' | 'dialog' | 'file' | 'async'
  targetSelector: string
  expectedSelector: string
  tabName?: string
  fieldSelector?: string
  fieldValue?: string
  setupFields?: readonly { selector: string; value: string }[]
  setupClicks?: readonly string[]
  triggerSelector?: string
  remountViaSelector?: string
  remountViaReadySelector?: string
  remountReturnSelector?: string
  dialogSelector?: string
  fileSelector?: string
  endpoint?: { method: 'GET' | 'POST'; path: string; emptyBody: unknown; expectedBody?: unknown }
  protectedSelector?: string
  asyncFixture?: 'pending' | 'empty-200' | 'error-503' | 'stale-503' | 'denied-403'
  refreshStrategy?: 'focus' | 'reload'
  pendingKeepsProtected?: boolean
  deniedKeepsProtected?: boolean
  deniedKeepsUntilResponse?: boolean
  reloadAfterDenied?: boolean
  clearCacheBeforeReload?: boolean
  completedHidesProtected?: boolean
  completedProtectedValue?: string
  routeSearch?: string
}

type ExecutableState = Pick<RouteStateEvidence, 'fixture' | 'selector' | 'assertion'> & {
  auth?: RouteStateEvidence['auth']
  actorRole?: CoverageAudience
  trigger?: RouteStateEvidence['trigger']
}

const visible = (selector: string, description: string, options: Partial<ExecutableState> = {}): ExecutableState => ({
  fixture: 'loaded', selector, assertion: { kind: 'visible', description }, ...options,
})

/** Route-level cases that can be reproduced without a mutation or physical device. */
const EXECUTABLE: Partial<Record<AppRouteId, Record<string, ExecutableState>>> = {
  login: { credentials: visible('form.rp-auth__card', 'The unauthenticated login form exposes its credential controls.', { auth: 'unauthenticated', actorRole: 'guest' }) },
  register: { account: visible('form.rp-auth__card', 'The unauthenticated registration form exposes account and role controls.', { auth: 'unauthenticated', actorRole: 'guest' }) },
  overview: {
    'attention-queue': visible('[data-testid="overview-attention-queue"]', 'The loaded overview renders the attention queue for an operator.', { actorRole: 'operator' }),
    'quick-actions': visible('a[href^="/work"]', 'The loaded overview renders a concrete work navigation action.', { actorRole: 'operator' }),
  },
  'operator-parks': {
    'current-parks': visible('.park-card-title:has-text("Северный парк")', 'The operator fixture renders the assigned park by name.', { actorRole: 'operator' }),
    'request-park': visible('[role="dialog"]', 'The request-park action opens the named modal dialog.', { actorRole: 'operator', trigger: { role: 'button', name: 'Запросить парк' } }),
    request: visible('[role="dialog"] form', 'The request-park dialog contains the park request form.', { actorRole: 'operator', trigger: { role: 'button', name: 'Запросить парк' } }),
  },
  work: { queue: visible('.rp-work-entities', 'The mechanic work fixture renders the task queue.', { actorRole: 'mechanic' }) },
  'work-issue': {
    repair: visible('[role="tab"]:has-text("Задача")', 'The task fixture exposes the main repair workflow tab.', { actorRole: 'mechanic' }),
    check: visible('#work-panel-check[role="tabpanel"]', 'Opening the robot-check tab renders its concrete tab panel.', { actorRole: 'mechanic', trigger: { role: 'tab', name: 'Проверка' } }),
    chat: visible('section[aria-label="Чат задачи"]', 'Opening task history exposes the collaboration section.', { actorRole: 'mechanic', trigger: { role: 'button', name: 'История и сообщения' } }),
  },
  robots: { search: visible('.rp-robots-search-panel', 'The robot route renders its VIN and task search form.', { actorRole: 'mechanic' }) },
  'robot-detail': { summary: visible('h2:has-text("Робот 447")', 'The robot detail fixture renders the selected robot summary.', { actorRole: 'mechanic' }) },
  'robot-check': { state: visible('[role="tabpanel"]', 'The robot-check fixture renders the selected state tab panel.', { actorRole: 'mechanic' }) },
  'legacy-robot-check': { 'resolve-robot': visible('.rp-robots-search-panel', 'The legacy emergency route renders the shared robot resolver.', { actorRole: 'mechanic' }) },
  inventory: { parts: visible('text=ABC-1', 'The inventory parts tab renders the deterministic catalog article.', { actorRole: 'mechanic' }) },
  reports: { mine: visible('button[aria-label^="Открыть репорт"]', 'The reports list renders the current user report card.', { actorRole: 'mechanic' }) },
  'reports-new': {
    report: visible('label:has-text("Заголовок *") input', 'The create-report route renders the report form title field.', { actorRole: 'mechanic' }),
    attachment: visible('input[type="file"]', 'The create-report route exposes the actual attachment file control.', { actorRole: 'mechanic', fixture: 'loaded' }),
  },
  'report-detail': {
    attachment: visible('a[href="/api/reports/1/attachments/11"]', 'The report detail fixture renders the actual persisted attachment download.', { actorRole: 'mechanic' }),
    'hard-delete': visible('[role="alertdialog"]', 'A built-in manager opens the irreversible report confirmation dialog.', { actorRole: 'royal', trigger: { role: 'button', name: 'Удалить репорт' } }),
  },
  campaigns: { 'campaign-list': visible('h2:has-text("Осенняя сервисная кампания")', 'The campaign list fixture renders its campaign card.', { actorRole: 'mechanic' }) },
  'campaign-detail': { open: visible('text=ROBOPARK-42', 'The campaign detail fixture renders the open Tracker ticket.', { actorRole: 'mechanic' }) },
  analytics: {
    summary: visible('.rp-analytics-park', 'The operator analytics fixture renders the park summary.', { actorRole: 'operator' }),
    'park-comparison': visible('[aria-label="Параметры аналитики"]', 'The analytics route renders deterministic comparison controls.', { actorRole: 'operator' }),
  },
  system: {
    metrics: visible('section[aria-label="Пользователи"]', 'The system route renders current activity and host health even when history contains only one day.', { actorRole: 'royal' }),
    operations: visible('.rp-system-operations > section:has(h2:text-is("Управляемые операции"))', 'The royal system route renders capability-gated typed operations.', { actorRole: 'royal' }),
    confirmation: visible('[role="dialog"]:has-text("Подтвердить операцию")', 'An available typed operation opens the exact reauthorization dialog.', { actorRole: 'royal', trigger: { role: 'button', name: 'Собрать диагностику' } }),
  },
  admin: {
    users: visible('a[href^="/admin/users"]', 'The management landing page renders the users destination.', { actorRole: 'royal' }),
    roles: visible('a[href^="/admin/roles"]', 'The management landing page renders the roles destination.', { actorRole: 'royal' }),
  },
  'admin-settings': { integrations: visible('text=Tracker OAuth', 'The settings route renders the integrations section.', { actorRole: 'royal' }) },
  'admin-users': { accounts: visible('button[aria-label="Открыть аккаунт route-admin"]', 'The users route renders the deterministic account row.', { actorRole: 'royal' }) },
  'admin-roles': { roles: visible('text=Механик', 'The roles route renders the deterministic mechanic role.', { actorRole: 'royal' }) },
  'admin-tracker': { redirect: visible('.rp-work-entities', 'The legacy admin Tracker route redirects to the shared work queue.', { actorRole: 'royal' }) },
  'admin-robot-check': { sections: visible('button[aria-label="Открыть раздел Колёса"]', 'The diagnostic editor renders the deterministic section row.', { actorRole: 'royal', trigger: { role: 'tab', name: 'Разделы и поля' } }) },
}

const OWNER_CONTRACT_TRIGGER: Record<NestedStateKind, string> = {
  view: 'loaded route content', tab: 'named tab selection', dialog: 'named action opening its dialog',
  form: 'form fields and submission boundary', file: 'real file input or persisted attachment',
  loading: 'deferred initial API response', empty: 'successful empty API response',
  error: 'initial API rejection and retry boundary', stale: 'background refresh rejection after loaded data',
  denied: 'HTTP 403 refresh that removes protected data',
}

const OWNER_ACTOR: Partial<Record<AppRouteId, CoverageAudience>> = {
  overview: 'operator', 'operator-parks': 'operator', work: 'mechanic', 'work-issue': 'mechanic',
  assistant: 'mechanic',
  robots: 'mechanic', 'robot-detail': 'mechanic', 'robot-check': 'mechanic', 'legacy-robot-check': 'mechanic',
  inventory: 'mechanic', reports: 'mechanic', 'reports-new': 'mechanic', 'report-detail': 'operator',
  campaigns: 'royal', 'campaign-detail': 'royal', schedule: 'mechanic', analytics: 'operator',
  system: 'royal',
  admin: 'royal', 'admin-settings': 'royal', 'admin-users': 'royal', 'admin-roles': 'royal',
  'admin-tracker': 'royal', 'admin-robot-check': 'royal',
}

type RouteAsyncContract = {
  method: 'GET' | 'POST'
  path: string
  emptyBody: unknown
  protectedSelector: string
  fieldSelector?: string
  fieldValue?: string
  triggerSelector?: string
  refreshStrategy?: 'focus' | 'reload'
  pendingKeepsProtected?: boolean
}

const ROUTE_ASYNC_CONTRACTS: Partial<Record<AppRouteId, RouteAsyncContract>> = {
  overview: { method: 'GET', path: '/api/operations/overview', emptyBody: { park_id: 7, generated_at: '2026-09-02T09:00:00Z', timezone: 'Europe/Moscow', status_options: [], selected_status: 'all', counts: {}, tasks: [], tasks_total: 0, tasks_truncated: false, flow: { definition_version: 2, window_start: null, window_end: null, expected_buckets: 0, observed_buckets: 0, complete: false, legacy_buckets: 0, points: [] }, sla: { target_hours: null, evaluated_count: 0, unknown_count: 0, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false }, workload: null, operators: null }, protectedSelector: '[data-testid="overview-attention-queue"]' },
  'operator-parks': { method: 'GET', path: '/api/operator/parks', emptyBody: [], protectedSelector: '.park-card-title:has-text("\u0421\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043a")' },
  work: { method: 'GET', path: '/api/tracker/issues', emptyBody: { items: [], total: 0, limit: 50, offset: 0, has_more: false }, protectedSelector: 'button[aria-label^="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0437\u0430\u0434\u0430\u0447\u0443 ROBOPARK-42:"]' },
  'work-issue': { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42', emptyBody: null, protectedSelector: '.rp-work-detail-pane .issue-detail-key:text-is("ROBOPARK-42")' },
  assistant: { method: 'GET', path: '/api/ai/status', emptyBody: { supported: true, installed: true, enabled: true, ready: true, reason: null, model: 'fixture', backend: 'cuda', can_manage: false, counts: { documents: 0, candidates: 0, jobs: 0 } }, protectedSelector: 'h1:text-is("Локальный помощник")' },
  robots: { method: 'POST', path: '/api/emergency/resolve', emptyBody: { vin: 'YASADR00000000447', sections: [] }, protectedSelector: '.rp-robots-recent-card', fieldSelector: '#robot-reference', fieldValue: '447', triggerSelector: 'button:has-text("\u041d\u0430\u0439\u0442\u0438 \u0440\u043e\u0431\u043e\u0442\u0430")' },
  'robot-detail': { method: 'GET', path: '/api/emergency/YASADR00000000447/snapshot', emptyBody: null, protectedSelector: '#robot-check-panel-map', refreshStrategy: 'reload', pendingKeepsProtected: true },
  'robot-check': { method: 'GET', path: '/api/emergency/YASADR00000000447/snapshot', emptyBody: null, protectedSelector: '[role="tabpanel"]:has-text("\u0417\u0430\u0440\u044f\u0434")', refreshStrategy: 'reload', pendingKeepsProtected: true },
  'legacy-robot-check': { method: 'POST', path: '/api/emergency/resolve', emptyBody: { vin: 'YASADR00000000447', sections: [] }, protectedSelector: '.rp-robots-recent-card', fieldSelector: '#robot-reference', fieldValue: '447', triggerSelector: 'button:has-text("\u041d\u0430\u0439\u0442\u0438 \u0440\u043e\u0431\u043e\u0442\u0430")' },
  inventory: { method: 'GET', path: '/api/inventory/catalog/search', emptyBody: { items: [], limit: 25, offset: 0, total: 0 }, protectedSelector: 'text=ABC-1', fieldSelector: '#inventory-part-search', fieldValue: 'ABC', pendingKeepsProtected: true },
  reports: { method: 'GET', path: '/api/reports/mine', emptyBody: [], protectedSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0440\u0435\u043f\u043e\u0440\u0442 \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043a\u043e\u043b\u0435\u0441\u043e"]' },
  'report-detail': { method: 'GET', path: '/api/reports/1', emptyBody: null, protectedSelector: 'text=\u0420\u043e\u0431\u043e\u0442 \u0442\u0440\u0435\u0431\u0443\u0435\u0442 \u043e\u0441\u043c\u043e\u0442\u0440\u0430.' },
  campaigns: { method: 'GET', path: '/api/campaigns', emptyBody: [], protectedSelector: 'h2:has-text("\u041e\u0441\u0435\u043d\u043d\u044f\u044f \u0441\u0435\u0440\u0432\u0438\u0441\u043d\u0430\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f")' },
  'campaign-detail': { method: 'GET', path: '/api/campaigns/4', emptyBody: null, protectedSelector: 'text=ROBOPARK-42' },
  analytics: { method: 'GET', path: '/api/analytics', emptyBody: [], protectedSelector: '.rp-analytics-park' },
  admin: { method: 'GET', path: '/api/admin/users', emptyBody: [], protectedSelector: 'a[href^="/admin/users"]' },
  'admin-settings': { method: 'GET', path: '/api/admin/settings/integrations', emptyBody: { tracker_token_masked: null, tracker_token_updated_at: null, emergency_cookie_masked: null, emergency_cookie_updated_at: null, emergency_cookie_valid: null, emergency_cookie_status: null, emergency_cookie_checked_at: null, emergency_cookie_checked_robot: null }, protectedSelector: 'dt:text-is("Tracker OAuth")' },
  'admin-users': { method: 'GET', path: '/api/admin/users', emptyBody: [], protectedSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0430\u043a\u043a\u0430\u0443\u043d\u0442 route-admin"]' },
  'admin-roles': { method: 'GET', path: '/api/admin/roles', emptyBody: [], protectedSelector: 'text=\u041c\u0435\u0445\u0430\u043d\u0438\u043a' },
  'admin-tracker': { method: 'GET', path: '/api/tracker/issues', emptyBody: { items: [], total: 0, limit: 50, offset: 0, has_more: false }, protectedSelector: 'button[aria-label^="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0437\u0430\u0434\u0430\u0447\u0443 ROBOPARK-42:"]' },
  'admin-robot-check': { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [], protectedSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0440\u0430\u0437\u0434\u0435\u043b \u041a\u043e\u043b\u0451\u0441\u0430"]' },
}

const TARGETS: Partial<Record<AppRouteId, Record<string, Omit<OwnerStateDriver, 'stateKey' | 'stateKind'>>>> = {
  assistant: {
    chat: { action: 'tab', tabName: 'Помощник', targetSelector: '#tab-chat', expectedSelector: '#assistant-panel-chat' },
    knowledge: { action: 'tab', tabName: 'База знаний', targetSelector: '#tab-knowledge', expectedSelector: '#assistant-panel-knowledge' },
  },
  schedule: {
    week: { action: 'button', targetSelector: '[aria-label="Масштаб календаря"] button:has-text("Неделя")', expectedSelector: '.rp-schedule-calendar__days--week' },
    month: { action: 'button', targetSelector: '[aria-label="Масштаб календаря"] button:has-text("Месяц")', expectedSelector: '.rp-schedule-calendar__days--month' },
    period: { action: 'form', targetSelector: 'button:has-text("Добавить период")', expectedSelector: '.rp-schedule__editor', triggerSelector: 'button:has-text("Добавить период")', fieldSelector: 'input[aria-label="Начало"]', fieldValue: '2026-09-21T09:00' },
    notifications: { action: 'assert', targetSelector: 'h2:has-text("Уведомления")', expectedSelector: 'h2:has-text("Уведомления")' },
  },
  login: { unauthorized: { action: 'assert', targetSelector: 'form.rp-auth__card', expectedSelector: 'form.rp-auth__card' } },
  register: { pending: { action: 'assert', targetSelector: 'form.rp-auth__card', expectedSelector: 'form.rp-auth__card' } },
  'change-password': {
    password: { action: 'form', targetSelector: 'form.login-card', expectedSelector: 'form.login-card', fieldSelector: 'input[autocomplete="current-password"]', fieldValue: 'Fixture-current-1' },
    error: { action: 'assert', targetSelector: 'form.login-card', expectedSelector: 'form.login-card' },
  },
  'no-cabinet': { denied: { action: 'assert', targetSelector: 'h1:has-text("\u041a\u0430\u0431\u0438\u043d\u0435\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d")', expectedSelector: 'h1:has-text("\u041a\u0430\u0431\u0438\u043d\u0435\u0442 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d")' } },
  'access-pending': { pending: { action: 'assert', targetSelector: 'h1:has-text("\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u043e\u0434\u043e\u0431\u0440\u0435\u043d\u0438\u044f")', expectedSelector: 'h1:has-text("\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u043e\u0434\u043e\u0431\u0440\u0435\u043d\u0438\u044f")' } },
  'access-rejected': { denied: { action: 'assert', targetSelector: 'h1:has-text("\u0414\u043e\u0441\u0442\u0443\u043f \u043e\u0442\u043a\u043b\u043e\u043d\u0451\u043d")', expectedSelector: 'h1:has-text("\u0414\u043e\u0441\u0442\u0443\u043f \u043e\u0442\u043a\u043b\u043e\u043d\u0451\u043d")' } },
  'mechanic-no-park': {
    empty: { action: 'assert', targetSelector: 'h1:has-text("\u041f\u0430\u0440\u043a \u043d\u0435 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d")', expectedSelector: 'h1:has-text("\u041f\u0430\u0440\u043a \u043d\u0435 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d")' },
    denied: { action: 'assert', targetSelector: 'h1:has-text("\u041f\u0430\u0440\u043a \u043d\u0435 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d")', expectedSelector: 'h1:has-text("\u041f\u0430\u0440\u043a \u043d\u0435 \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d")' },
  },
  'work-issue': {
    repair: { action: 'tab', tabName: '\u0417\u0430\u0434\u0430\u0447\u0430', targetSelector: '[role="tab"]:has-text("\u0417\u0430\u0434\u0430\u0447\u0430")', expectedSelector: '#work-panel-task' },
    check: { action: 'tab', tabName: 'Проверка', targetSelector: '[role="tab"]:text-is("Проверка")', expectedSelector: '#work-panel-check' },
    chat: { action: 'button', targetSelector: 'button:text-is("История и сообщения")', expectedSelector: 'section[aria-label="Чат задачи"]' },
    'open-related': { action: 'tab', tabName: '\u041e\u0442\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438', targetSelector: '[role="tab"]:has-text("\u041e\u0442\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438")', expectedSelector: '#work-panel-open' },
    'closed-related': { action: 'tab', tabName: '\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438', targetSelector: '[role="tab"]:has-text("\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438")', expectedSelector: '#work-panel-closed' },
    comment: { action: 'form', targetSelector: 'textarea[aria-label="\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438"]', expectedSelector: 'textarea[aria-label="\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438"]', fieldSelector: 'textarea[aria-label="\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438"]', fieldValue: 'evidence-comment' },
    photo: { action: 'file', targetSelector: '.issue-attach-group input[aria-label="\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u0444\u043e\u0442\u043e"]', expectedSelector: '.issue-attach-group input[aria-label="\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u0444\u043e\u0442\u043e"]', fileSelector: '.issue-attach-group input[aria-label="\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u0444\u043e\u0442\u043e"]' },
  },
  work: {
    queue: { action: 'button', targetSelector: 'button:has-text("\u041e\u0447\u0435\u0440\u0435\u0434\u044c")', expectedSelector: 'button:has-text("\u041e\u0447\u0435\u0440\u0435\u0434\u044c")' },
    mine: { action: 'button', targetSelector: 'button:has-text("\u041c\u043e\u0438 \u0437\u0430\u0434\u0430\u0447\u0438")', expectedSelector: 'button:has-text("\u041c\u043e\u0438 \u0437\u0430\u0434\u0430\u0447\u0438")' },
    filters: { action: 'form', targetSelector: '.rp-work-filters__status select', expectedSelector: '.rp-work-filters__status select', fieldSelector: '.rp-work-filters__status select', fieldValue: 'new' },
  },
  robots: {
    empty: { action: 'assert', targetSelector: 'p:text-is("\u041d\u0435\u0434\u0430\u0432\u043d\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0445 \u0440\u043e\u0431\u043e\u0442\u043e\u0432 \u043d\u0435\u0442.")', expectedSelector: 'p:text-is("\u041d\u0435\u0434\u0430\u0432\u043d\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0445 \u0440\u043e\u0431\u043e\u0442\u043e\u0432 \u043d\u0435\u0442.")' },
    camera: { action: 'file', targetSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', expectedSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', fileSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")' },
  },
  'legacy-robot-check': {
    empty: { action: 'assert', targetSelector: 'p:text-is("\u041d\u0435\u0434\u0430\u0432\u043d\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0445 \u0440\u043e\u0431\u043e\u0442\u043e\u0432 \u043d\u0435\u0442.")', expectedSelector: 'p:text-is("\u041d\u0435\u0434\u0430\u0432\u043d\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0445 \u0440\u043e\u0431\u043e\u0442\u043e\u0432 \u043d\u0435\u0442.")' },
    camera: { action: 'file', targetSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', expectedSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', fileSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")' },
  },
  inventory: {
    ...Object.fromEntries([['parts', '\u0417\u0430\u043f\u0447\u0430\u0441\u0442\u0438'], ['receipts', '\u041f\u043e\u0441\u0442\u0430\u0432\u043a\u0438'], ['counts', '\u0418\u043d\u0432\u0435\u043d\u0442\u0430\u0440\u0438\u0437\u0430\u0446\u0438\u044f'], ['manage', '\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435'], ['export', '\u0412\u044b\u0433\u0440\u0443\u0437\u043a\u0430']].map(([id, name]) => [id, { action: 'tab', tabName: name, targetSelector: `[role="tab"]:has-text("${name}")`, expectedSelector: `[data-inventory-workflow="${id}"]` }])),
    labels: { action: 'file', tabName: '\u0417\u0430\u043f\u0447\u0430\u0441\u0442\u0438', targetSelector: 'button:has-text("\u041f\u0435\u0447\u0430\u0442\u0430\u0442\u044c \u044d\u0442\u0438\u043a\u0435\u0442\u043a\u0443")', expectedSelector: 'button:has-text("\u041f\u0435\u0447\u0430\u0442\u0430\u0442\u044c \u044d\u0442\u0438\u043a\u0435\u0442\u043a\u0443")', fileSelector: 'button:has-text("\u041f\u0435\u0447\u0430\u0442\u0430\u0442\u044c \u044d\u0442\u0438\u043a\u0435\u0442\u043a\u0443")' },
    stock: { action: 'form', tabName: '\u0417\u0430\u043f\u0447\u0430\u0441\u0442\u0438', triggerSelector: 'button:has-text("\u041d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u043e\u0441\u0442\u0430\u0442\u043e\u043a")', targetSelector: '#stock-location-101', expectedSelector: '#stock-location-101', fieldSelector: '#stock-location-101', fieldValue: '\u041f\u043e\u043b\u043a\u0430 E-1' },
    receipt: { action: 'form', tabName: '\u041f\u043e\u0441\u0442\u0430\u0432\u043a\u0438', triggerSelector: 'button:has-text("\u041d\u043e\u0432\u0430\u044f \u043f\u043e\u0441\u0442\u0430\u0432\u043a\u0430")', targetSelector: '#receipt-supplier', expectedSelector: '#receipt-supplier', fieldSelector: '#receipt-supplier', fieldValue: 'Evidence supplier' },
    count: { action: 'form', tabName: '\u0418\u043d\u0432\u0435\u043d\u0442\u0430\u0440\u0438\u0437\u0430\u0446\u0438\u044f', triggerSelector: 'button:has-text("\u041d\u043e\u0432\u0430\u044f \u0438\u043d\u0432\u0435\u043d\u0442\u0430\u0440\u0438\u0437\u0430\u0446\u0438\u044f")', targetSelector: '#count-name', expectedSelector: '#count-name', fieldSelector: '#count-name', fieldValue: 'Evidence count' },
  },
  reports: {
    mine: { action: 'tab', tabName: '\u041c\u043e\u0438 \u0440\u0435\u043f\u043e\u0440\u0442\u044b', targetSelector: '[role="tab"]:has-text("\u041c\u043e\u0438 \u0440\u0435\u043f\u043e\u0440\u0442\u044b")', expectedSelector: '#reports-mine-panel' },
    inbox: { action: 'tab', tabName: '\u0412\u0445\u043e\u0434\u044f\u0449\u0438\u0435', targetSelector: '[role="tab"]:has-text("\u0412\u0445\u043e\u0434\u044f\u0449\u0438\u0435")', expectedSelector: '#reports-inbox-panel' },
    filters: { action: 'form', targetSelector: 'select[aria-label="\u0421\u0442\u0430\u0442\u0443\u0441 \u0440\u0435\u043f\u043e\u0440\u0442\u043e\u0432"]', expectedSelector: 'select[aria-label="\u0421\u0442\u0430\u0442\u0443\u0441 \u0440\u0435\u043f\u043e\u0440\u0442\u043e\u0432"]', fieldSelector: 'select[aria-label="\u0421\u0442\u0430\u0442\u0443\u0441 \u0440\u0435\u043f\u043e\u0440\u0442\u043e\u0432"]', fieldValue: 'open' },
  },
  'reports-new': {
    kind: { action: 'button', targetSelector: '.reports-forms button:text-is("\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u0430")', expectedSelector: '.reports-forms button.is-active:text-is("\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u0430")' },
    loading: { action: 'async', targetSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', expectedSelector: '.reports-forms button[type="submit"]:disabled .spinner-label:text-is("\u0421\u043e\u0437\u0434\u0430\u0442\u044c")', protectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', setupFields: [{ selector: 'input[placeholder="ROBOPARK-123"]', value: 'ROBOPARK-42' }, { selector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', value: 'Evidence report' }], triggerSelector: '.reports-forms button[type="submit"]', endpoint: { method: 'POST', path: '/api/reports', emptyBody: {}, expectedBody: { kind: 'ticket_question', park_id: 7, title: 'Evidence report', body: '', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42' } }, asyncFixture: 'pending', pendingKeepsProtected: true },
    empty: { action: 'async', targetSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', expectedSelector: 'p.alert-success:has-text("\u0420\u0435\u043f\u043e\u0440\u0442 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0443.")', protectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', setupFields: [{ selector: 'input[placeholder="ROBOPARK-123"]', value: 'ROBOPARK-42' }, { selector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', value: 'Evidence report' }], triggerSelector: '.reports-forms button[type="submit"]', endpoint: { method: 'POST', path: '/api/reports', emptyBody: { id: 77, kind: 'ticket_question', status: 'open', park_id: 7, author_user_id: 101, target_role: 'operator', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42', title: 'Evidence report', body: '', parent_report_id: null, return_comment: null, created_at: '2026-09-02T09:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null, attachments: [] }, expectedBody: { kind: 'ticket_question', park_id: 7, title: 'Evidence report', body: '', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42' } }, asyncFixture: 'empty-200', completedProtectedValue: '' },
    error: { action: 'async', targetSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', expectedSelector: 'p.alert-error:has-text("Не удалось выполнить действие. Попробуйте ещё раз.")', protectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', setupFields: [{ selector: 'input[placeholder="ROBOPARK-123"]', value: 'ROBOPARK-42' }, { selector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', value: 'Evidence report' }], triggerSelector: '.reports-forms button[type="submit"]', endpoint: { method: 'POST', path: '/api/reports', emptyBody: {}, expectedBody: { kind: 'ticket_question', park_id: 7, title: 'Evidence report', body: '', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42' } }, asyncFixture: 'error-503' },
    stale: { action: 'async', targetSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', expectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', protectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', setupFields: [{ selector: 'input[placeholder="ROBOPARK-123"]', value: 'ROBOPARK-42' }, { selector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', value: 'Evidence report' }], triggerSelector: '.reports-forms button[type="submit"]', endpoint: { method: 'POST', path: '/api/reports', emptyBody: {}, expectedBody: { kind: 'ticket_question', park_id: 7, title: 'Evidence report', body: '', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42' } }, asyncFixture: 'stale-503' },
    denied: { action: 'async', targetSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', expectedSelector: 'p.alert-error:has-text("\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u043f\u0440\u0430\u0432.")', protectedSelector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', setupFields: [{ selector: 'input[placeholder="ROBOPARK-123"]', value: 'ROBOPARK-42' }, { selector: 'label:has-text("\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a *") input', value: 'Evidence report' }], triggerSelector: '.reports-forms button[type="submit"]', endpoint: { method: 'POST', path: '/api/reports', emptyBody: {}, expectedBody: { kind: 'ticket_question', park_id: 7, title: 'Evidence report', body: '', tracker_key: 'ROBOPARK-42', tracker_url: 'https://st.yandex-team.ru/ROBOPARK-42' } }, asyncFixture: 'denied-403', deniedKeepsProtected: true },
  },
  'report-detail': { return: { action: 'form', routeSearch: '?park=7&pane=inbox', triggerSelector: '.report-detail-actions button:text-is("\u0412\u0435\u0440\u043d\u0443\u0442\u044c")', targetSelector: 'textarea[placeholder="\u041f\u043e\u0447\u0435\u043c\u0443 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442\u0435 \u043c\u0435\u0445\u0430\u043d\u0438\u043a\u0443"]', expectedSelector: '.issue-comment-form', fieldSelector: 'textarea[placeholder="\u041f\u043e\u0447\u0435\u043c\u0443 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442\u0435 \u043c\u0435\u0445\u0430\u043d\u0438\u043a\u0443"]', fieldValue: 'evidence-return' } },
  campaigns: {
    denied: { action: 'async', targetSelector: 'h2:has-text("\u041e\u0441\u0435\u043d\u043d\u044f\u044f \u0441\u0435\u0440\u0432\u0438\u0441\u043d\u0430\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f")', expectedSelector: 'text=\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430', protectedSelector: 'h2:has-text("\u041e\u0441\u0435\u043d\u043d\u044f\u044f \u0441\u0435\u0440\u0432\u0438\u0441\u043d\u0430\u044f \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044f")', endpoint: { method: 'GET', path: '/api/campaigns', emptyBody: [] }, asyncFixture: 'denied-403', refreshStrategy: 'reload', deniedKeepsUntilResponse: false },
    create: { action: 'form', targetSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', expectedSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', fieldSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', fieldValue: 'Evidence campaign' },
  },
  'campaign-detail': {
    closed: { action: 'assert', targetSelector: 'button:text-is("\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0435 \u00b7 0")', expectedSelector: 'p:text-is("\u0417\u0430\u043a\u0440\u044b\u0442\u044b\u0445 \u0442\u0438\u043a\u0435\u0442\u043e\u0432 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.")' },
    denied: { action: 'async', targetSelector: 'text=ROBOPARK-42', expectedSelector: 'text=\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430', protectedSelector: 'text=ROBOPARK-42', endpoint: { method: 'GET', path: '/api/campaigns/4', emptyBody: null }, asyncFixture: 'denied-403', refreshStrategy: 'reload', deniedKeepsUntilResponse: false },
    photo: { action: 'file', targetSelector: 'label:has-text("\u0424\u043e\u0442\u043e") input[type="file"]', expectedSelector: 'label:has-text("\u0424\u043e\u0442\u043e") input[type="file"]', fileSelector: 'label:has-text("\u0424\u043e\u0442\u043e") input[type="file"]', setupClicks: ['button:has-text("\u0417\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443")'] },
    settings: { action: 'form', targetSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', expectedSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', fieldSelector: 'label:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', fieldValue: 'Evidence campaign' },
    'ticket-result': { action: 'form', targetSelector: 'label:has-text("\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439 \u0434\u043b\u044f \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0430") textarea', expectedSelector: 'label:has-text("\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439 \u0434\u043b\u044f \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0430") textarea', fieldSelector: 'label:has-text("\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439 \u0434\u043b\u044f \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0430") textarea', fieldValue: 'Evidence result', setupClicks: ['button:has-text("\u0417\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443")'] },
  },
  analytics: {
    flow: { action: 'assert', targetSelector: '.rp-analytics-coverage:has-text("\u041f\u043e\u0442\u043e\u043a:")', expectedSelector: '.rp-analytics-coverage:has-text("\u041f\u043e\u0442\u043e\u043a:")' },
    sla: { action: 'assert', targetSelector: 'h3:text-is("\u0414\u0438\u043d\u0430\u043c\u0438\u043a\u0430 SLA")', expectedSelector: 'article:has(h4:text-is("\u0414\u043e\u043b\u044f \u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043d\u044b\u0445 \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0439")) p:text-is("\u0414\u043b\u044f \u0433\u0440\u0430\u0444\u0438\u043a\u0430 \u0435\u0449\u0451 \u043d\u0435\u0442 \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0439.")' },
    stale: { action: 'async', targetSelector: '.rp-analytics-park', expectedSelector: '.rp-analytics-warning[role="alert"]:has-text("Сервис временно недоступен")', protectedSelector: '.rp-analytics-park', endpoint: { method: 'GET', path: '/api/analytics', emptyBody: [] }, triggerSelector: 'button:has-text("Обновить аналитику")', asyncFixture: 'stale-503' },
    denied: { action: 'async', targetSelector: '.rp-analytics-park', expectedSelector: 'text=\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430', protectedSelector: '.rp-analytics-park', endpoint: { method: 'GET', path: '/api/analytics', emptyBody: [] }, asyncFixture: 'denied-403', refreshStrategy: 'reload', deniedKeepsUntilResponse: false },
  },
  admin: {
    users: { action: 'assert', targetSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href^="/admin/users"]', expectedSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href^="/admin/users"]' },
    roles: { action: 'assert', targetSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href^="/admin/roles"]', expectedSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href^="/admin/roles"]' },
    parks: { action: 'assert', targetSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href*="tab=parks"]', expectedSelector: 'nav[aria-label="\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0443\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f"] a[href*="tab=parks"]' },
  },
  'admin-settings': {
    stale: { action: 'async', targetSelector: 'dt:text-is("Tracker OAuth")', expectedSelector: 'p.alert-warning:has-text("Интеграции")', protectedSelector: 'dt:text-is("Tracker OAuth")', endpoint: { method: 'GET', path: '/api/admin/settings/integrations', emptyBody: null }, asyncFixture: 'stale-503', remountViaSelector: 'nav[aria-label="Разделы управления"] a[href^="/admin?"]', remountViaReadySelector: 'h1:text-is("Управление")', remountReturnSelector: 'nav[aria-label="Разделы управления"] a[href^="/admin/settings?"]:not([href*="tab="])' },
    denied: { action: 'async', targetSelector: 'dt:text-is("Tracker OAuth")', expectedSelector: '[role="status"]:has-text("Состояние защиты не загружено")', protectedSelector: 'dt:text-is("Tracker OAuth")', endpoint: { method: 'GET', path: '/api/admin/settings/integrations', emptyBody: null }, asyncFixture: 'denied-403', refreshStrategy: 'reload', deniedKeepsUntilResponse: false },
    'tracker-policy': { action: 'form', targetSelector: 'label.toggle:has-text("Оператор видит неразмеченные тикеты") input[type="checkbox"]', expectedSelector: 'label.toggle:has-text("Оператор видит неразмеченные тикеты") input[type="checkbox"]', fieldSelector: 'label.toggle:has-text("Оператор видит неразмеченные тикеты") input[type="checkbox"]', fieldValue: 'true' },
    registration: { action: 'form', targetSelector: 'input[placeholder="Задайте или смените пароль для /register"]', expectedSelector: 'input[placeholder="Задайте или смените пароль для /register"]', fieldSelector: 'input[placeholder="Задайте или смените пароль для /register"]', fieldValue: 'Evidence-password-1' },
  },
  'admin-users': {
    activity: { action: 'button', targetSelector: 'button[aria-label="Открыть аккаунт route-admin"]', expectedSelector: '[aria-label="Последняя активность"]' },
    permissions: { action: 'form', targetSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', expectedSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', fieldSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', fieldValue: 'true', setupClicks: ['button[aria-label="Открыть аккаунт route-admin"]'] },
  },
  'admin-roles': {
    denied: { action: 'async', targetSelector: 'text=Механик', expectedSelector: 'text=Загрузка ролей…', protectedSelector: 'text=Механик', endpoint: { method: 'GET', path: '/api/admin/roles', emptyBody: [] }, asyncFixture: 'denied-403', refreshStrategy: 'reload', deniedKeepsUntilResponse: false },
    permissions: { action: 'form', targetSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', expectedSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', fieldSelector: 'label.admin-perm-check:has-text("Склад") input[type="checkbox"]', fieldValue: 'true', setupClicks: ['button[aria-label="Открыть роль Механик"]'] },
  },
  'admin-robot-check': {
    loading: { action: 'async', routeSearch: '?park=7&tab=fields', targetSelector: 'button[aria-label="Открыть раздел Колёса"]', expectedSelector: '.skeleton-list[role="status"]', protectedSelector: 'button[aria-label="Открыть раздел Колёса"]', endpoint: { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [] }, asyncFixture: 'pending' },
    empty: { action: 'async', routeSearch: '?park=7&tab=fields', targetSelector: 'button[aria-label="Открыть раздел Колёса"]', expectedSelector: 'p:text-is("Разделы проверки робота ещё не настроены")', protectedSelector: 'button[aria-label="Открыть раздел Колёса"]', endpoint: { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [] }, asyncFixture: 'empty-200', completedHidesProtected: true },
    error: { action: 'async', routeSearch: '?park=7&tab=fields', targetSelector: 'button[aria-label="Открыть раздел Колёса"]', expectedSelector: '.rp-error-state:has-text("Не удалось загрузить разделы")', protectedSelector: 'button[aria-label="Открыть раздел Колёса"]', endpoint: { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [] }, asyncFixture: 'error-503', completedHidesProtected: true },
    stale: { action: 'async', routeSearch: '?park=7&tab=fields', targetSelector: 'button[aria-label="Открыть раздел Колёса"]', expectedSelector: 'p.alert-error:has-text("Не удалось загрузить данные")', protectedSelector: 'button[aria-label="Открыть раздел Колёса"]', endpoint: { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [] }, asyncFixture: 'stale-503', remountViaSelector: 'a.page-back[href="/admin"]', remountViaReadySelector: 'h1:text-is("Управление")', remountReturnSelector: 'nav[aria-label="Основная навигация"] a[href="/admin/emergency/config"]' },
    denied: { action: 'async', routeSearch: '?park=7&tab=fields', targetSelector: 'button[aria-label="Открыть раздел Колёса"]', expectedSelector: '.rp-error-state:has-text("Каталог недоступен")', protectedSelector: 'button[aria-label="Открыть раздел Колёса"]', endpoint: { method: 'GET', path: '/api/admin/emergency/sections', emptyBody: [] }, asyncFixture: 'denied-403', refreshStrategy: 'reload', clearCacheBeforeReload: true, deniedKeepsUntilResponse: false },
    sections: { action: 'tab', tabName: '\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0438 \u043f\u043e\u043b\u044f', targetSelector: '[role="tab"]:text-is("\u0420\u0430\u0437\u0434\u0435\u043b\u044b \u0438 \u043f\u043e\u043b\u044f")', expectedSelector: '#check-settings-fields' },
    readings: { action: 'tab', tabName: '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f', targetSelector: '#tab-readings', expectedSelector: '#check-settings-readings' },
    rules: { action: 'tab', tabName: '\u041e\u0448\u0438\u0431\u043a\u0438', targetSelector: '[role="tab"]:has-text("\u041e\u0448\u0438\u0431\u043a\u0438")', expectedSelector: '[role="tabpanel"][aria-label="\u041a\u0430\u0442\u0430\u043b\u043e\u0433 \u043e\u0448\u0438\u0431\u043e\u043a"]' },
    unknowns: { action: 'tab', tabName: '\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438', setupClicks: ['[role="tab"]:has-text("\u041e\u0448\u0438\u0431\u043a\u0438")'], targetSelector: '[role="tab"]:has-text("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438")', expectedSelector: '[role="tabpanel"][aria-label="\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438"]' },
    rule: { action: 'form', tabName: '\u041e\u0448\u0438\u0431\u043a\u0438', triggerSelector: 'button:has-text("\u041d\u043e\u0432\u043e\u0435 \u043f\u0440\u0430\u0432\u0438\u043b\u043e")', targetSelector: 'input[id$="-diagnostic-title"]', expectedSelector: 'input[id$="-diagnostic-title"]', fieldSelector: 'input[id$="-diagnostic-title"]', fieldValue: 'Evidence rule' },
  },
  'robot-detail': {
    ...Object.fromEntries([['summary', '\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435'], ['map', '\u041a\u0430\u0440\u0442\u0430']].map(([id, name]) => [id, { action: 'tab', tabName: name, targetSelector: `[role="tab"]:has-text("${name}")`, expectedSelector: '[role="tabpanel"]' }])),
    tasks: { action: 'button', triggerSelector: '[data-testid="robot-check-layout"] button[aria-haspopup="menu"]', targetSelector: '[role="menuitem"]:text-is("\u0417\u0430\u0434\u0430\u0447\u0438")', expectedSelector: '[role="tab"]:text-is("\u0417\u0430\u0434\u0430\u0447\u0438")' },
  } as Record<string, Omit<OwnerStateDriver, 'stateKey' | 'stateKind'>>,
  'robot-check': {
    ...Object.fromEntries([['state', '\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435'], ['errors', '\u041e\u0448\u0438\u0431\u043a\u0438']].map(([id, name]) => [id, { action: 'tab', tabName: name, targetSelector: `[role="tab"]:has-text("${name}")`, expectedSelector: '[role="tabpanel"]' }])),
    readings: { action: 'button', triggerSelector: '[data-testid="robot-check-layout"] button[aria-haspopup="menu"]', targetSelector: '[role="menuitem"]:text-is("\u0422\u0435\u043b\u0435\u043c\u0435\u0442\u0440\u0438\u044f")', expectedSelector: '[role="tab"]:text-is("\u0422\u0435\u043b\u0435\u043c\u0435\u0442\u0440\u0438\u044f")' },
  } as Record<string, Omit<OwnerStateDriver, 'stateKey' | 'stateKind'>>,
}

const NOT_APPLICABLE: Readonly<Record<string, string>> = {
  'route-coverage:admin:loading': 'ManagementPage is a synchronous navigation landing and owns no loading request or asynchronous data controller.',
  'route-coverage:admin:empty': 'ManagementPage derives visible destinations synchronously from authenticated permissions and owns no empty API collection.',
  'route-coverage:admin:error': 'ManagementPage performs no domain request, so it has no route-owned API error state to execute.',
  'route-coverage:admin:stale': 'ManagementPage owns neither cached server data nor a revalidation controller, so a stale response cannot occur.',
  'route-coverage:admin:denied': 'ManagementPage performs no domain request; route authorization is enforced before this static landing mounts.',
  'route-coverage:admin:requests': 'ManagementPage exposes requests only inside the Parks settings destination and has no requests tab or panel of its own.',
  'route-coverage:campaigns:stale': 'CampaignList performs one initial GET and exposes no cache revalidation or loaded-state refresh action, so stale list UI cannot occur.',
  'route-coverage:campaign-detail:stale': 'CampaignDetail performs one initial detail GET; its Tracker refresh is a distinct POST mutation and cannot revalidate that GET into stale detail UI.',
  'route-coverage:not-found:not-found': 'The wildcard CatchAll redirects an authenticated user to their role landing page and an unauthenticated user to login; no not-found view or state owner is mounted.',
}

function ownerDriver(routeId: AppRouteId, stateId: string, kind: NestedStateKind): OwnerStateDriver {
  const stateKey = `route-coverage:${routeId}:${stateId}`
  if (routeId === 'system' && ['loading', 'empty', 'error', 'stale', 'denied'].includes(kind)) {
    const fixtureByKind = { loading: 'pending', empty: 'empty-200', error: 'error-503', stale: 'stale-503', denied: 'denied-403' } as const
    const expectedSelector = kind === 'loading' ? '.rp-loading-state[aria-label="Загружаем состояние системы"]'
      : kind === 'empty' ? 'section[aria-label="Пользователи"] dd:text-is("0")'
        : 'p.alert-error:has-text("Не удалось получить свежие данные")'
    return {
      stateKey, stateKind: kind, action: 'async', targetSelector: 'section[aria-label="Пользователи"]',
      expectedSelector, protectedSelector: 'section[aria-label="Пользователи"]',
      endpoint: { method: 'GET', path: '/api/admin/system/summary', emptyBody: { sampled_at: null, metrics_stale: false, worker_health: 'worker_heartbeat_missing', online: { total: 0, by_role: {}, by_park: {} }, sync: { cursor_age_seconds: null, pending_action_count: 0, oldest_pending_action_age_seconds: null, retry_count: 0, needs_attention_count: 0, last_success_at: null, last_error: null, worker_lease_state: 'unknown' }, push: { pending: 0, needs_attention: 0 }, metrics: null, release: {} } },
      asyncFixture: fixtureByKind[kind as keyof typeof fixtureByKind],
    }
  }
  if (routeId === 'schedule' && ['loading', 'empty', 'error', 'stale', 'denied'].includes(kind)) {
    const fixtureByKind = { loading: 'pending', empty: 'empty-200', error: 'error-503', stale: 'stale-503', denied: 'denied-403' } as const
    const expectedSelector = kind === 'loading' ? '.rp-loading-state[aria-label="Загружаем график"]'
      : kind === 'empty' ? '#schedule-panel-mine .rp-schedule-day-cards:has-text("На выбранный день периодов нет")'
        : kind === 'stale' ? '.rp-error-state:has-text("Не удалось обновить график")'
          : '.rp-error-state:has-text("Не удалось загрузить график")'
    return {
      stateKey, stateKind: kind, action: 'async', targetSelector: '#schedule-panel-mine',
      expectedSelector, protectedSelector: '#schedule-panel-mine',
      endpoint: { method: 'GET', path: '/api/schedules', emptyBody: [] },
      asyncFixture: fixtureByKind[kind as keyof typeof fixtureByKind],
      remountViaSelector: kind === 'stale' || kind === 'denied' ? 'nav[aria-label="Основная навигация"]:visible a[href="/work"]' : undefined,
      remountViaReadySelector: kind === 'stale' || kind === 'denied' ? 'h1:has-text("Работа")' : undefined,
      remountReturnSelector: kind === 'stale' || kind === 'denied' ? 'nav[aria-label="Основная навигация"]:visible a[href="/schedule"]' : undefined,
    }
  }
  const dialogDrivers: Record<string, Omit<OwnerStateDriver, 'stateKey' | 'stateKind'>> = {
    'work-issue:review': { action: 'button', targetSelector: '.issue-actions button:has-text("\u041f\u0435\u0440\u0435\u0434\u0430\u0442\u044c \u043d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0443")', expectedSelector: 'form:has(select[aria-label="Что случилось?"])' },
    'robots:scanner': { action: 'dialog', targetSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', expectedSelector: '[role="dialog"]:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u043e\u0431\u043e\u0442\u0430")', triggerSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', dialogSelector: '[role="dialog"]:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u043e\u0431\u043e\u0442\u0430")' },
    'legacy-robot-check:scanner': { action: 'dialog', targetSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', expectedSelector: '[role="dialog"]:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u043e\u0431\u043e\u0442\u0430")', triggerSelector: 'button:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c")', dialogSelector: '[role="dialog"]:has-text("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0440\u043e\u0431\u043e\u0442\u0430")' },
    'robot-check:ignore-error': { action: 'dialog', tabName: '\u041e\u0448\u0438\u0431\u043a\u0438', targetSelector: '[role="tabpanel"]', expectedSelector: '[role="tabpanel"]', triggerSelector: '[role="tab"]:has-text("\u041e\u0448\u0438\u0431\u043a\u0438")', dialogSelector: '[role="tabpanel"]' },
    'inventory:post-confirmation': { action: 'dialog', tabName: '\u041f\u043e\u0441\u0442\u0430\u0432\u043a\u0438', targetSelector: 'button:has-text("\u041d\u043e\u0432\u0430\u044f \u043f\u043e\u0441\u0442\u0430\u0432\u043a\u0430")', expectedSelector: '#receipt-supplier', triggerSelector: 'button:has-text("\u041d\u043e\u0432\u0430\u044f \u043f\u043e\u0441\u0442\u0430\u0432\u043a\u0430")', dialogSelector: '#receipt-supplier' },
    'report-detail:resolve': { action: 'button', routeSearch: '?park=7&pane=inbox', targetSelector: '.report-detail-actions button:text-is("\u0413\u043e\u0442\u043e\u0432\u043e")', expectedSelector: 'text=\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043e.', endpoint: { method: 'POST', path: '/api/reports/1/done', emptyBody: {} } },
    'campaigns:parks': { action: 'dialog', targetSelector: 'button[aria-haspopup="dialog"]:has-text("\u041f\u0430\u0440\u043a\u0438 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438")', expectedSelector: '[role="dialog"][aria-label="\u041f\u0430\u0440\u043a\u0438 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438"]', triggerSelector: 'button[aria-haspopup="dialog"]:has-text("\u041f\u0430\u0440\u043a\u0438 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438")', dialogSelector: '[role="dialog"][aria-label="\u041f\u0430\u0440\u043a\u0438 \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u0438"]' },
    'campaign-detail:delete': { action: 'dialog', targetSelector: 'button:has-text("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e")', expectedSelector: 'browser:\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e?', triggerSelector: 'button:has-text("\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e")', dialogSelector: 'browser:\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043c\u043f\u0430\u043d\u0438\u044e?' },
    'admin-users:user': { action: 'dialog', targetSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0430\u043a\u043a\u0430\u0443\u043d\u0442 route-admin"]', expectedSelector: 'section[aria-label="\u041e\u043f\u0430\u0441\u043d\u044b\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f"]', triggerSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0430\u043a\u043a\u0430\u0443\u043d\u0442 route-admin"]', dialogSelector: 'section[aria-label="\u041e\u043f\u0430\u0441\u043d\u044b\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f"]' },
    'admin-roles:role': { action: 'dialog', targetSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0440\u043e\u043b\u044c \u041c\u0435\u0445\u0430\u043d\u0438\u043a"]', expectedSelector: 'label.field:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input', triggerSelector: 'button[aria-label="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0440\u043e\u043b\u044c \u041c\u0435\u0445\u0430\u043d\u0438\u043a"]', dialogSelector: 'label.field:has-text("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435") input' },
    'admin-robot-check:preview': { action: 'dialog', tabName: '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f', targetSelector: 'button:has-text("\u041d\u043e\u0432\u043e\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0435")', expectedSelector: 'section[aria-label="\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f"]', triggerSelector: 'button:has-text("\u041d\u043e\u0432\u043e\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0435")', dialogSelector: 'section[aria-label="\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f"]' },
    'admin-robot-check:ignore': { action: 'dialog', tabName: '\u041e\u0448\u0438\u0431\u043a\u0438', targetSelector: '[role="tab"]:text-is("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438")', expectedSelector: '[role="tabpanel"][aria-label="\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438"]', triggerSelector: '[role="tab"]:text-is("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438")', dialogSelector: '[role="tabpanel"][aria-label="\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438"]' },
  }
  const dialogDriver = dialogDrivers[`${routeId}:${stateId}`]
  if (dialogDriver) return { stateKey, stateKind: kind, ...dialogDriver }
  if (routeId === 'robot-check' && stateId === 'camera') return { stateKey, stateKind: kind, action: 'file', tabName: '\u0421\u0445\u0435\u043c\u0430', targetSelector: '.rp-check-scheme-host', expectedSelector: '.rp-check-scheme-host', fileSelector: '.rp-check-scheme-host' }
  if (['loading', 'empty', 'error', 'stale', 'denied'].includes(kind)) {
    const standalone = TARGETS[routeId]?.[stateId]
    if (standalone) return { stateKey, stateKind: kind, ...standalone }
    const endpoint = ROUTE_ASYNC_CONTRACTS[routeId]
    if (!endpoint) throw new Error(`Missing async owner driver for ${stateKey}`)
    const fixtureByKind = { loading: 'pending', empty: 'empty-200', error: 'error-503', stale: 'stale-503', denied: 'denied-403' } as const
    return { stateKey, stateKind: kind, action: 'async', targetSelector: endpoint.protectedSelector, expectedSelector: endpoint.protectedSelector, endpoint: { method: endpoint.method, path: endpoint.path, emptyBody: endpoint.emptyBody }, protectedSelector: endpoint.protectedSelector, fieldSelector: endpoint.fieldSelector, fieldValue: endpoint.fieldValue, triggerSelector: endpoint.triggerSelector, refreshStrategy: endpoint.refreshStrategy, pendingKeepsProtected: endpoint.pendingKeepsProtected, reloadAfterDenied: kind === 'denied' && (routeId === 'robots' || routeId === 'legacy-robot-check'), asyncFixture: fixtureByKind[kind as keyof typeof fixtureByKind] }
  }
  const explicit = TARGETS[routeId]?.[stateId]
  if (explicit) return { stateKey, stateKind: kind, ...explicit }
  throw new Error(`Missing explicit owner driver for ${stateKey}`)
}

function exactOwner(routeId: AppRouteId, stateId: string, kind: NestedStateKind) {
  const stateKey = `route-coverage:${routeId}:${stateId}`
  return {
    ownerTest: {
      path: 'apps/web/e2e/operational/route-owner-contracts.spec.ts',
      title: `${stateKey} [Классический]`,
      stateKey,
    },
    ownerContract: `${stateKey} asserts ${OWNER_CONTRACT_TRIGGER[kind]} for the ${routeId} ${kind} state; the test rejects a mismatched route, state key, kind, trigger, or title.`,
    ownerDriver: ownerDriver(routeId, stateId, kind),
  }
}

export const ROUTE_STATE_EVIDENCE: readonly RouteStateEvidence[] = ROUTE_COVERAGE_MANIFEST.flatMap(route =>
  route.states.map(state => {
    const executable = EXECUTABLE[route.routeId]?.[state.id]
    if (executable) return {
      caseId: state.testId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: executable.auth ?? 'authenticated', actorRole: executable.actorRole ?? route.roles[0],
      fixture: executable.fixture, selector: executable.selector, trigger: executable.trigger, assertion: executable.assertion,
    } satisfies RouteStateEvidence
    const caseId = state.testId
    const notApplicableReason = NOT_APPLICABLE[caseId]
    if (notApplicableReason) return {
      caseId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: 'authenticated', actorRole: OWNER_ACTOR[route.routeId] ?? route.roles[0], fixture: 'not-applicable', selector: '',
      assertion: { kind: 'visible', description: `${caseId} is absent because this route has no semantic owner for the declared state.` },
      notApplicableReason,
      ownerTest: { path: 'apps/web/src/app/routing/routeCoverageManifest.test.ts', title: `${caseId} semantic absence`, stateKey: caseId },
      ownerContract: `${caseId} is explicitly audited as semantically impossible; adding an API or state owner requires replacing this absence contract.`,
    } satisfies RouteStateEvidence
    const delegated = exactOwner(route.routeId, state.id, state.kind)
    const delegatedActor = route.routeId === 'reports' && state.id === 'inbox'
      ? 'operator'
      : (OWNER_ACTOR[route.routeId] ?? route.roles[0])
    return {
      caseId: state.testId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: route.roles.includes('guest') ? 'unauthenticated' : 'authenticated',
      actorRole: route.roles.includes('guest') ? 'guest' : delegatedActor, fixture: 'owner-test', selector: '',
      assertion: { kind: 'visible', description: `${route.routeId} delegates ${state.kind} state ${state.id} to the exact owning test.` },
      ...delegated,
    } satisfies RouteStateEvidence
  }),
)
