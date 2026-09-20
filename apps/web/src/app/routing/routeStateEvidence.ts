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
    'attention-queue': visible('h2:has-text("Очередь внимания")', 'The loaded overview renders the attention queue for an operator.', { actorRole: 'operator' }),
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
    check: visible('#work-panel-check[role="tabpanel"]', 'Opening the robot-check tab renders its concrete tab panel.', { actorRole: 'mechanic', trigger: { role: 'tab', name: 'Проверка робота' } }),
    chat: visible('h3:has-text("Комментарии")', 'The task fixture exposes the collaboration section in the task workflow.', { actorRole: 'mechanic' }),
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
  admin: {
    users: visible('a[href^="/admin/users"]', 'The management landing page renders the users destination.', { actorRole: 'royal' }),
    roles: visible('a[href^="/admin/roles"]', 'The management landing page renders the roles destination.', { actorRole: 'royal' }),
  },
  'admin-settings': { integrations: visible('text=Tracker OAuth', 'The settings route renders the integrations section.', { actorRole: 'royal' }) },
  'admin-users': { accounts: visible('button[aria-label="Открыть аккаунт route-admin"]', 'The users route renders the deterministic account row.', { actorRole: 'royal' }) },
  'admin-roles': { roles: visible('text=Механик', 'The roles route renders the deterministic mechanic role.', { actorRole: 'royal' }) },
  'admin-tracker': { redirect: visible('.rp-work-entities', 'The legacy admin Tracker route redirects to the shared work queue.', { actorRole: 'royal' }) },
  'admin-robot-check': { sections: visible('button[aria-label="Открыть раздел Колёса"]', 'The diagnostic editor renders the deterministic section row.', { actorRole: 'royal' }) },
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
  robots: 'mechanic', 'robot-detail': 'mechanic', 'robot-check': 'mechanic', 'legacy-robot-check': 'mechanic',
  inventory: 'mechanic', reports: 'mechanic', 'reports-new': 'mechanic', 'report-detail': 'operator',
  campaigns: 'royal', 'campaign-detail': 'royal', analytics: 'operator',
  admin: 'royal', 'admin-settings': 'royal', 'admin-users': 'royal', 'admin-roles': 'royal',
  'admin-tracker': 'royal', 'admin-robot-check': 'royal',
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
    const delegated = exactOwner(route.routeId, state.id, state.kind)
    return {
      caseId: state.testId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: route.roles.includes('guest') ? 'unauthenticated' : 'authenticated',
      actorRole: route.roles.includes('guest') ? 'guest' : (OWNER_ACTOR[route.routeId] ?? route.roles[0]), fixture: 'owner-test', selector: '',
      assertion: { kind: 'visible', description: `${route.routeId} delegates ${state.kind} state ${state.id} to the exact owning test.` },
      ...delegated,
    } satisfies RouteStateEvidence
  }),
)
