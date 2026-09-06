import { expect, type Page } from '@playwright/test'
import type {
  Blocker, DashboardSummary, EmergencySectionDetail, EmergencySnapshot, MechanicTasks,
  OperationsOverview, OperatorBlockers, Paged, Park, TrackerActionResult, TrackerComment,
  TrackerIssueDetail, TrackerTransition, User,
} from '../../src/api'
import { installMockApi, type MockRoute } from '../support/mockApi'

export const FIXED_TIME = '2026-09-02T09:00:00Z'
export const parkNorth: Park = { id: 7, name: 'Северный парк', tag: 'north', is_active: true, tracker_queue: 'ROBOPARK', tracker_priority: 'normal', tracker_type: 'task', group_id: null, chat_id: null, feature_reports: true, feature_blockers: true, feature_sla_repair: false, feature_backlog_alerts: false }
export const parkSouth: Park = { ...parkNorth, id: 8, name: 'Южный парк', tag: 'south' }
export const roles = ['mechanic', 'operator', 'driver', 'admin', 'royal'] as const
export type OperationalRole = typeof roles[number]

const allPermissions = [
  'nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.map',
  'nav.analytics', 'nav.reports', 'nav.learning', 'nav.help', 'nav.admin',
  'nav.admin.tracker', 'nav.admin.emergency', 'tracker.read', 'tracker.write',
  'tracker.attach', 'reports.create', 'reports.resolve', 'roles.manage',
  'users.manage', 'users.approve', 'parks.manage',
]

const rolePermissions: Record<OperationalRole, string[]> = {
  driver: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports', 'tracker.read', 'reports.create'],
  mechanic: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports', 'tracker.read', 'tracker.write', 'tracker.attach', 'reports.create'],
  operator: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.analytics', 'nav.reports', 'tracker.read', 'tracker.write', 'tracker.attach', 'reports.create', 'reports.resolve'],
  admin: allPermissions.filter((permission) => permission !== 'users.approve'),
  royal: allPermissions,
}

export function userForRole(role: OperationalRole): User {
  return {
    id: 100 + roles.indexOf(role), username: `${role}-e2e`, role, access_status: 'approved',
    tracker_login: role === 'driver' ? null : `${role}.test`, must_change_password: false, screenshot_guard: false,
    permissions: rolePermissions[role],
    parks: [parkNorth, parkSouth],
  }
}

export const snapshot: EmergencySnapshot = {
  vin: 'YASADR00000000447', short_number: '447', observed_at: FIXED_TIME, online: true,
  speed: 0, charge_percent: 84, battery1_percent: 85, battery2_percent: 83, disk_percent: 24,
  mode: 'Автономный', icp_label: 'Подключён', icp_ok: true, lte_label: 'Подключён', lte_ok: true,
  connection: 'lte', error_banner: null, lat: 55.751244, lon: 37.618423, heading_deg: 90, wheels_fault: ['fl'],
}
export const issue: TrackerIssueDetail = {
  key: 'ROBOPARK-42', summary: 'Проверить переднее левое колесо робота 447', status: 'Открыт', status_key: 'open',
  queue: 'ROBOPARK', robot: '447', created_at: FIXED_TIME, updated_at: FIXED_TIME, hours_created: '0',
  url: 'https://tracker.example.invalid/ROBOPARK-42', tags: ['north'], priority: 'normal', type: 'task',
  assignee: { display: 'Механик смены', login: 'mechanic.test' }, resolution: null,
  description: 'Проверить крепление и состояние переднего левого колеса перед выездом.',
  reporter: { display: 'Оператор смены', login: 'operator.test' }, components: ['Колёса'], attachments: [],
  capabilities: { comment: true, assign: true, unassign: true, transition: true, close: true, attach: true },
}
export function summaryForPark(parkId: number): DashboardSummary {
  return { park_id: parkId, generated_at: FIXED_TIME, arrived: 12, done: 8, queued: parkId === 8 ? 9 : 3, in_transit: 2, moving: [{ key: issue.key, summary: issue.summary }] }
}

export type OperationalOptions = {
  issue?: TrackerIssueDetail
  snapshot?: EmergencySnapshot
  listCount?: number
  user?: User
}

function operationsOverview(user: User, request: Request): OperationsOverview {
  const parkId = Number(new URL(request.url).searchParams.get('park_id'))
  const status = new URL(request.url).searchParams.get('status') ?? 'all'
  const allowed = user.role === 'driver' ? ['new', 'moving']
    : user.role === 'mechanic' ? ['queued', 'diagnostics']
      : ['new', 'moving', 'queued', 'diagnostics', 'waiting_team', 'waiting_parts', 'other']
  const bucket = allowed[0]
  const task: Blocker = {
    key: issue.key, summary: issue.summary, status: bucket === 'queued' ? 'Очередь' : 'Новый',
    status_key: bucket, robot: issue.robot ?? null, created_at: FIXED_TIME,
    hours_created: '0', url: issue.url, bucket,
  }
  const tasks = status === 'all' || status === bucket ? [task] : []
  const leadership = ['operator', 'admin', 'royal'].includes(user.role)
  return {
    park_id: parkId, generated_at: FIXED_TIME, timezone: 'Europe/Moscow',
    status_options: [{ key: 'all', label: 'Все доступные' }, ...allowed.map((key) => ({ key, label: key }))],
    selected_status: status, counts: { all: 1, ...Object.fromEntries(allowed.map((key) => [key, key === bucket ? 1 : 0])) },
    tasks, tasks_total: tasks.length, tasks_truncated: false,
    flow: { definition_version: 2, window_start: '2026-09-01T09:00:00Z', window_end: FIXED_TIME, expected_buckets: 12, observed_buckets: 2, complete: false, legacy_buckets: 0, points: [
      { bucket_start: '2026-09-02T05:00:00Z', arrived_count: 1, departed_count: 0 },
      { bucket_start: '2026-09-02T07:00:00Z', arrived_count: 0, departed_count: 1 },
    ] },
    sla: { target_hours: null, evaluated_count: 0, unknown_count: 1, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false },
    workload: leadership ? [{ login: user.tracker_login ?? null, display: user.username, open_count: 1, overdue_count: null, oldest_hours: 0 }] : null,
    operators: user.role === 'admin' || user.role === 'royal' ? [{ user_id: user.id, username: user.username, tracker_login: user.tracker_login ?? null, open_count: 1, overdue_count: null, oldest_hours: 0 }] : null,
  }
}
export function operationalRoutes(options: OperationalOptions = {}): MockRoute[] {
  let currentIssue = structuredClone(options.issue ?? issue)
  const currentSnapshot = structuredClone(options.snapshot ?? snapshot)
  const comments: TrackerComment[] = []
  const user = options.user ?? userForRole('mechanic')
  const blocker = (): Blocker => ({ key: currentIssue.key, summary: currentIssue.summary, status: currentIssue.status, status_key: currentIssue.status_key, robot: currentIssue.robot ?? null, created_at: FIXED_TIME, hours_created: '0', url: currentIssue.url, bucket: 'open', priority: 'normal', assignee: currentIssue.assignee })
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/robots', handler: request => {
      const params = new URL(request.url).searchParams
      const query = (params.get('query') ?? '').toUpperCase()
      const matches = (!query || ['447', currentSnapshot.vin, currentIssue.key].includes(query)) && !params.get('active_errors')?.includes('true') && !['online', 'offline'].includes(params.get('state') ?? '')
      const items = matches ? [{ vin: currentSnapshot.vin, short_number: '447', park_ids: [Number(params.get('park_id') ?? 7)], state: 'unknown', telemetry: null, error_count: null, task_count: 1, task_keys: [currentIssue.key], issue_keys: [currentIssue.key] }] : []
      return { json: { items, total: items.length, offset: 0, limit: 50, has_more: false, partial: true, source_complete: true, source: 'scoped_tracker_issues', park_id: Number(params.get('park_id') ?? 7) } }
    } },
    { method: 'GET', path: '/api/operations/overview', handler: request => ({ json: operationsOverview(user, request) }) },
    { method: 'GET', path: '/api/dashboard/summary', handler: request => ({ json: summaryForPark(Number(new URL(request.url).searchParams.get('park_id'))) }) },
    { method: 'GET', path: '/api/tracker/issues', handler: request => {
      const params = new URL(request.url).searchParams
      const offset = Number(params.get('offset') ?? 0)
      const limit = Number(params.get('limit') ?? 50)
      const items = Array.from({ length: options.listCount ?? 1 }, (_, index) => index === 0 ? currentIssue : { ...currentIssue, key: `ROBOPARK-${100 + offset + index}`, summary: `Плановая проверка робота ${100 + index}` })
      const total = options.listCount ? 101 : 51
      return { json: { items, total, limit, offset, has_more: offset + limit < total } satisfies Paged<TrackerIssueDetail> }
    } },
    { method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-42$/, handler: () => ({ json: currentIssue }) },
    { method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-42\/comments$/, handler: () => ({ json: comments }) },
    { method: 'GET', path: /^\/api\/tracker\/transitions\/ROBOPARK-42$/, handler: () => ({ json: [{ id: 'resolve', display: 'Решить' }] satisfies TrackerTransition[] }) },
    { method: 'GET', path: '/api/tracker/users', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/mechanic/tasks', handler: () => ({ json: { park_tag: 'north', status: 'all', counts: { open: 1 }, items: [blocker()] } satisfies MechanicTasks }) },
    { method: 'GET', path: '/api/operator/blockers', handler: () => ({ json: { park_id: 7, park_tag: 'north', status: 'all', counts: { open: 1 }, items: [blocker()] } satisfies OperatorBlockers }) },
    { method: 'GET', path: /^\/api\/(mechanic|operator|tracker)\/robots\/[^/]+\/tickets$/, handler: () => ({ json: { query: currentSnapshot.vin, items: [blocker()] } satisfies { query: string; items: Blocker[] } }) },
    { method: 'POST', path: '/api/emergency/resolve', handler: () => ({ json: { vin: currentSnapshot.vin, sections: [{ id: 'wheels', title: 'Колёса' }] } }) },
    { method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: () => ({ json: currentSnapshot }) },
    { method: 'GET', path: /^\/api\/emergency\/[^/]+\/sections\/wheels$/, handler: () => ({ json: { id: 'wheels', title: 'Колёса', fields: [{ label: 'Переднее левое', lines: ['Неисправность: требуется проверка'] }] } satisfies EmergencySectionDetail }) },
    { method: 'GET', path: '/api/admin/users', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/access-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/parks', handler: () => ({ json: [parkNorth, parkSouth] }) },
  ]
  for (const action of ['comment', 'attachments', 'assign', 'unassign', 'transition', 'close'] as const) {
    routes.push({ method: 'POST', path: new RegExp(`^/api/tracker/issues/ROBOPARK-42/${action}$`), handler: async request => {
      if (action === 'comment') {
        const body: { text: string } = await request.json()
        comments.push({ id: `comment-${comments.length + 1}`, text: body.text, author: 'Механик смены', author_login: 'mechanic.test', created_at: FIXED_TIME, attachments: [] })
      }
      if (action === 'assign') {
        const body: { assignee: string } = await request.json()
        currentIssue = { ...currentIssue, assignee: { login: body.assignee, display: body.assignee } }
      }
      if (action === 'unassign') currentIssue = { ...currentIssue, assignee: null }
      if (action === 'attachments') {
        const file = (await request.formData()).get('file')
        if (file instanceof File) currentIssue = { ...currentIssue, attachments: [...(currentIssue.attachments ?? []), { id: 'attachment-1', name: file.name, size: file.size, mimetype: file.type, url: null }] }
      }
      if (action === 'transition' || action === 'close') currentIssue = { ...currentIssue, status: 'Закрыт', status_key: 'closed' }
      return { json: { key: issue.key, action, status: 'ok', actor: 'mechanic.test', performed_at: FIXED_TIME } satisfies TrackerActionResult }
    } })
  }
  return routes
}

export async function installOperational(page: Page, options: OperationalOptions & { role?: OperationalRole; user?: User; parks?: Park[]; routes?: MockRoute[] } = {}) {
  await page.clock.setFixedTime(new Date('2026-09-02T09:05:00Z'))
  await page.route(/^https?:\/\/(?!localhost(?=[:/])|127\.0\.0\.1(?=[:/]))/, route => route.abort())
  const user = options.user ?? userForRole(options.role ?? 'mechanic')
  const routes = operationalRoutes({ issue: options.issue, snapshot: options.snapshot, listCount: options.listCount, user })
  await installMockApi(page, { user, parks: options.parks ?? user.parks, routes: [...(options.routes ?? []), ...routes] })
}

export async function settlePage(page: Page) {
  await expect(page.locator('.global-progress')).toHaveAttribute('aria-hidden', 'true')
  await page.evaluate(async () => {
    await document.fonts.ready
    // Replacing a loading state can cancel its transition; cancellation is settled too.
    await Promise.allSettled(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished))
  })
}
