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
  ownerTest?: { path: string; title: string }
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
  'report-detail': { attachment: visible('a[href="/api/reports/1/attachments/11"]', 'The report detail fixture renders the actual persisted attachment download.', { actorRole: 'mechanic' }) },
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

const owner = (path: string, title: string) => ({ path, title })
const OWNER_TEST: Record<AppRouteId, { path: string; title: string }> = {
  home: owner('apps/web/src/app/routing/AppRouter.test.tsx', 'keeps catch-all redirect-to-landing semantics'),
  login: owner('apps/web/src/pages/AuthPages.test.tsx', 'gives unauthenticated users context without branding and a working theme choice'),
  register: owner('apps/web/src/pages/AuthPages.test.tsx', 'uses the same product shell for registration and explains the approval flow'),
  'change-password': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'allows an approved user to voluntarily open the change-password form'),
  'no-cabinet': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'renders one top-level fallback while shell authentication is loading'),
  'access-pending': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'gates pending access before mounting the shell'),
  'access-rejected': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'redirects an already authorized user away from stale standalone access screens'),
  'mechanic-no-park': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'gates a mechanic without a park before mounting the shell'),
  overview: owner('apps/web/src/domains/shift/OverviewPage.test.tsx', 'retains cached overview content and offers retry after deferred offline revalidation'),
  'operator-parks': owner('apps/web/e2e/operational/route-role-layout.spec.ts', 'route fixtures prove loaded operator, campaign, report-detail and administration workflows'),
  work: owner('apps/web/src/domains/work/IssueWorkbench.test.tsx', 'keeps cached protected work visible only for a transient revalidation failure'),
  'work-issue': owner('apps/web/src/domains/work/IssueWorkbench.test.tsx', 'gives a non-retainable detail-side failure priority over transient stale data'),
  robots: owner('apps/web/src/domains/robots/RobotResolver.test.tsx', 'offers camera scanning and manual entry without BarcodeDetector'),
  'robot-detail': owner('apps/web/src/domains/robots/RobotDetailView.test.tsx', 'keeps same-scope 403 fail-closed across same-ID auth publication and temporary park loading'),
  'robot-check': owner('apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx', 'retains the last snapshot offline and after partial failures, with local retry'),
  'legacy-robot-check': owner('apps/web/src/domains/robots/RobotWorkspace.test.tsx', 'legacy check URL exposes the same identity and related tasks with its selected photo tab'),
  inventory: owner('apps/web/src/domains/inventory/InventoryPage.test.tsx', 'keeps tabs and export usable when the optional overview KPI request fails'),
  reports: owner('apps/web/src/pages/Reports.test.tsx', 'keeps mobile report filters and summaries visible while deferring detail history'),
  'reports-new': owner('apps/web/src/pages/Reports.test.tsx', 'clears a draft synchronously when effective access changes at the same principal and park'),
  'report-detail': owner('apps/web/src/pages/Reports.test.tsx', 'keeps a desktop report list beside its detail and returns to the filtered URL'),
  campaigns: owner('apps/web/src/domains/campaigns/CampaignsPage.test.tsx', 'labels active, completed and overdue campaigns with accessible text'),
  'campaign-detail': owner('apps/web/src/domains/campaigns/CampaignsPage.test.tsx', 'keeps the previous campaign visible when a refresh fails'),
  analytics: owner('apps/web/src/domains/analytics/AnalyticsWorkspace.test.tsx', 'keeps historical content during an offline background refresh and allows retry'),
  admin: owner('apps/web/src/domains/management/ManagementPage.test.tsx', 'shows only granted management modules on the hub'),
  'admin-settings': owner('apps/web/src/pages/Admin.test.tsx', 'shows an unavailable cookie warning with a retry action'),
  'admin-users': owner('apps/web/src/domains/management/UserManagementPage.test.tsx', 'refreshes the account list automatically without replacing an unsaved account draft'),
  'admin-roles': owner('apps/web/src/domains/management/RoleManagementPage.test.tsx', 'refreshes role rows on focus while keeping the open role draft'),
  'admin-tracker': owner('apps/web/src/pages/AdminTrackerWorkspace.test.tsx', 'redirects the retired Tracker workspace to Work without reviving manual filters'),
  'admin-robot-check': owner('apps/web/src/pages/AdminEmergencyConfig.test.tsx', 'shows section search and identities before one explicitly selected editor at 390px'),
  'not-found': owner('apps/web/src/app/routing/AppRouter.test.tsx', 'keeps catch-all redirect-to-landing semantics'),
}

export const ROUTE_STATE_EVIDENCE: readonly RouteStateEvidence[] = ROUTE_COVERAGE_MANIFEST.flatMap(route =>
  route.states.map(state => {
    const executable = EXECUTABLE[route.routeId]?.[state.id]
    if (executable) return {
      caseId: state.testId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: executable.auth ?? 'authenticated', actorRole: executable.actorRole ?? route.roles[0],
      fixture: executable.fixture, selector: executable.selector, trigger: executable.trigger, assertion: executable.assertion,
    } satisfies RouteStateEvidence
    return {
      caseId: state.testId, routeId: route.routeId, stateId: state.id, kind: state.kind,
      auth: route.roles.includes('guest') ? 'unauthenticated' : 'authenticated',
      actorRole: route.roles.includes('guest') ? 'guest' : route.roles[0], fixture: 'owner-test', selector: '',
      assertion: { kind: 'visible', description: `${route.routeId} delegates ${state.kind} state ${state.id} to the exact owning test.` },
      ownerTest: OWNER_TEST[route.routeId],
      ownerContract: `${route.routeId}:${state.id} is asserted by its owning component suite, including the route-specific ${state.kind} boundary without a duplicate browser fixture.`,
    } satisfies RouteStateEvidence
  }),
)
