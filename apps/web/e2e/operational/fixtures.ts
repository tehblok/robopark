import { expect, type Page } from '@playwright/test'
import type {
  Blocker, DashboardSummary, EmergencySectionDetail, EmergencySnapshot, MechanicTasks,
  InventoryCatalogComponent, InventoryCatalogSearchItem, InventoryCount, InventoryCountLineInput,
  InventoryCountScope, InventoryOverview, InventoryReceipt, InventoryReceiptInput, InventoryStockView,
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
  'nav.analytics', 'nav.reports', 'nav.inventory', 'nav.learning', 'nav.help', 'nav.admin',
  'nav.admin.tracker', 'nav.admin.emergency', 'tracker.read', 'tracker.write',
  'tracker.attach', 'reports.create', 'reports.resolve', 'roles.manage',
  'inventory.stock.manage', 'inventory.documents.post', 'inventory.export', 'inventory.catalog.manage',
  'users.manage', 'users.approve', 'parks.manage',
]

const rolePermissions: Record<OperationalRole, string[]> = {
  driver: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports', 'tracker.read', 'reports.create'],
  mechanic: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports', 'nav.inventory', 'tracker.read', 'tracker.write', 'tracker.attach', 'reports.create', 'inventory.stock.manage', 'inventory.documents.post', 'inventory.export'],
  operator: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.analytics', 'nav.reports', 'nav.inventory', 'tracker.read', 'tracker.write', 'tracker.attach', 'reports.create', 'reports.resolve'],
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
  speed: 0, charge_percent: 84, battery1_percent: 85, battery2_percent: 83,
  battery1_connected: true, battery2_connected: true, disk_percent: 24,
  mode: 'Автономный', icp_label: 'Подключён', icp_ok: true, lte_label: 'Подключён', lte_ok: true,
  connection: 'lte', sim_signals: [4, 3], error_banner: null, lat: 55.751244, lon: 37.618423, heading_deg: 90, wheels_fault: ['fl'],
}
export const issue: TrackerIssueDetail = {
  key: 'ROBOPARK-42', summary: 'Проверить переднее левое колесо робота 447', status: 'Открыт', status_key: 'open',
  queue: 'ROBOPARK', robot: '447', created_at: FIXED_TIME, updated_at: FIXED_TIME, hours_created: '0',
  url: 'https://tracker.example.invalid/ROBOPARK-42', tags: ['north'], priority: 'normal', type: 'task',
  assignee: { display: 'Механик смены', login: 'mechanic-e2e' }, resolution: null,
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
  const inventoryPart: InventoryCatalogSearchItem = {
    id: 101, component_id: 11, component_name: 'Ходовая часть', name: 'Комплект крепежа', article: 'ABC-1',
    is_active: true, has_photo: false, quantity: '0', minimum_quantity: '2', location: 'Полка A-1', stock_is_active: true,
  }
  const inventoryComponent: InventoryCatalogComponent = { id: 11, name: inventoryPart.component_name, is_active: true, has_photo: false }
  type InventoryStockState = { quantity: bigint; minimumQuantity: `${bigint}`; location: string | null; isActive: boolean; version: bigint }
  const inventoryStocks = new Map<string, InventoryStockState>([
    [`${parkNorth.id}:${inventoryPart.id}`, { quantity: 0n, minimumQuantity: '2', location: 'Полка A-1', isActive: true, version: 1n }],
    [`${parkSouth.id}:${inventoryPart.id}`, { quantity: 3n, minimumQuantity: '2', location: 'Полка A-1', isActive: true, version: 1n }],
  ])
  const receipts: InventoryReceipt[] = []
  const counts: InventoryCount[] = []
  const requestedPark = (request: Request) => {
    const url = new URL(request.url)
    const match = url.pathname.match(/^\/api\/inventory\/parks\/(\d+)/)
    return Number(url.searchParams.get('park_id') ?? match?.[1] ?? 0)
  }
  const canReadPark = (parkId: number) => ['admin', 'royal'].includes(user.role) || user.parks.some(park => park.id === parkId)
  const inventoryDenied = { status: 403, json: { detail: 'inventory_park_forbidden' } }
  const stockFor = (parkId: number, catalogPartId = inventoryPart.id): InventoryStockState => {
    const key = `${parkId}:${catalogPartId}`
    const current = inventoryStocks.get(key)
    if (current) return current
    const created: InventoryStockState = { quantity: 0n, minimumQuantity: '2', location: null, isActive: true, version: 1n }
    inventoryStocks.set(key, created)
    return created
  }
  const searchItem = (parkId: number): InventoryCatalogSearchItem => {
    const stock = stockFor(parkId)
    return { ...inventoryPart, quantity: String(stock.quantity) as `${bigint}`, minimum_quantity: stock.minimumQuantity, location: stock.location, stock_is_active: stock.isActive }
  }
  const receiptFrom = (parkId: number, id: number, input: InventoryReceiptInput, status: InventoryReceipt['status'] = 'draft'): InventoryReceipt => ({
    id, park_id: parkId, supplier: input.supplier ?? null, document_number: input.document_number ?? null,
    received_on: input.received_on, comment: input.comment ?? null, status, created_by: user.id,
    posted_by: status === 'posted' ? user.id : null, created_at: FIXED_TIME, posted_at: status === 'posted' ? FIXED_TIME : null,
    lines: input.lines.map((line, index) => ({
      id: id * 10 + index + 1, catalog_part_id: line.catalog_part_id,
      catalog_part_name: inventoryPart.name, catalog_part_article: inventoryPart.article,
      catalog_component_id: inventoryPart.component_id, catalog_component_name: inventoryPart.component_name,
      quantity: line.quantity, note: line.note ?? null,
    })),
  })
  const countLines = (parkId: number, id: number) => [{
    id: id * 10 + 1, catalog_part_id: inventoryPart.id, catalog_part_name: inventoryPart.name,
    catalog_part_article: inventoryPart.article, catalog_component_id: inventoryPart.component_id,
    catalog_component_name: inventoryPart.component_name,
    expected_quantity: String(stockFor(parkId).quantity) as `${bigint}`,
    actual_quantity: null, difference: null, comment: null,
  }]
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
    { method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-42\/timeline$/, handler: () => ({ json: comments.map(comment => ({
      id: comment.id, kind: 'tracker', author: comment.author, text: comment.text,
      created_at: comment.created_at, sync_state: 'synced', attachments: comment.attachments ?? [],
    })) }) },
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
    { method: 'GET', path: '/api/inventory', handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const quantity = stockFor(parkId).quantity
      return { json: {
        park_id: parkId, component_count: 1, part_count: 1,
        low_stock_count: quantity < 2n ? 1 : 0, out_of_stock_count: quantity === 0n ? 1 : 0,
        components: [],
      } satisfies InventoryOverview }
    } },
    { method: 'GET', path: '/api/inventory/catalog/search', handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const query = new URL(request.url).searchParams.get('q')?.toUpperCase()
      const items = !query || `${inventoryPart.name} ${inventoryPart.article}`.toUpperCase().includes(query) ? [searchItem(parkId)] : []
      return { json: { items, limit: 25, offset: 0, total: items.length } }
    } },
    { method: 'GET', path: '/api/inventory/catalog/components', handler: request => {
      const parkId = requestedPark(request)
      return canReadPark(parkId) ? { json: { items: [inventoryComponent], limit: 100, offset: 0, total: 1 } } : inventoryDenied
    } },
    { method: 'GET', path: /^\/api\/inventory\/catalog\/parts\/101$/, handler: request => {
      const parkId = requestedPark(request)
      return canReadPark(parkId) ? { json: searchItem(parkId) } : inventoryDenied
    } },
    { method: 'PUT', path: /^\/api\/inventory\/parks\/\d+\/stocks\/101$/, handler: async request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const body: Pick<InventoryStockView, 'minimum_quantity' | 'location' | 'is_active'> = await request.json()
      const current = stockFor(parkId)
      const updated: InventoryStockState = { ...current, minimumQuantity: body.minimum_quantity, location: body.location, isActive: body.is_active, version: current.version + 1n }
      inventoryStocks.set(`${parkId}:${inventoryPart.id}`, updated)
      return { json: { park_id: parkId, catalog_part_id: inventoryPart.id, quantity: String(updated.quantity), minimum_quantity: updated.minimumQuantity, location: updated.location, is_active: updated.isActive, version: String(updated.version) } satisfies InventoryStockView }
    } },
    { method: 'GET', path: /^\/api\/inventory\/parks\/\d+\/receipts$/, handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const items = receipts.filter(item => item.park_id === parkId)
      return { json: { items, limit: 25, offset: 0, total: items.length } }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/receipts$/, handler: async request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const created = receiptFrom(parkId, receipts.length + 1, await request.json())
      receipts.unshift(created)
      return { json: created }
    } },
    { method: 'PATCH', path: /^\/api\/inventory\/parks\/\d+\/receipts\/\d+$/, handler: async request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const id = Number(new URL(request.url).pathname.split('/').at(-1))
      const index = receipts.findIndex(item => item.id === id && item.park_id === parkId)
      const updated = receiptFrom(parkId, id, await request.json())
      if (index >= 0) receipts[index] = updated
      else receipts.unshift(updated)
      return { json: updated }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/receipts\/\d+\/post$/, handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const id = Number(new URL(request.url).pathname.split('/').at(-2))
      const index = receipts.findIndex(item => item.id === id && item.park_id === parkId)
      const current = receipts[index]
      if (!current) return { status: 404, json: { detail: 'inventory_receipt_not_found' } }
      if (current.status === 'posted') return { json: current }
      if (current.status !== 'draft') return { status: 409, json: { detail: 'inventory_receipt_not_draft' } }
      current.lines.forEach(line => {
        const stock = stockFor(parkId, line.catalog_part_id)
        inventoryStocks.set(`${parkId}:${line.catalog_part_id}`, { ...stock, quantity: stock.quantity + BigInt(line.quantity), version: stock.version + 1n })
      })
      const posted = { ...current, status: 'posted' as const, posted_by: user.id, posted_at: FIXED_TIME }
      receipts[index] = posted
      return { json: posted }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/receipts\/\d+\/(cancel|reverse)$/, handler: request => {
      const parkId = requestedPark(request)
      const id = Number(new URL(request.url).pathname.split('/').at(-2))
      const index = receipts.findIndex(item => item.id === id && item.park_id === parkId)
      if (!canReadPark(parkId)) return inventoryDenied
      if (index < 0) return { status: 404, json: { detail: 'inventory_receipt_not_found' } }
      receipts[index] = { ...receipts[index], status: 'cancelled' }
      return { json: receipts[index] }
    } },
    { method: 'GET', path: /^\/api\/inventory\/parks\/\d+\/counts$/, handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const items = counts.filter(item => item.park_id === parkId)
      return { json: { items, limit: 25, offset: 0, total: items.length } }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/counts$/, handler: async request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const body: { name: string; scope: InventoryCountScope } = await request.json()
      const created: InventoryCount = { id: counts.length + 1, park_id: parkId, name: body.name, status: 'draft', created_by: user.id, posted_by: null, created_at: FIXED_TIME, posted_at: null, lines: countLines(parkId, counts.length + 1) }
      counts.unshift(created)
      return { json: created }
    } },
    { method: 'PATCH', path: /^\/api\/inventory\/parks\/\d+\/counts\/\d+$/, handler: async request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const id = Number(new URL(request.url).pathname.split('/').at(-1))
      const current = counts.find(item => item.id === id && item.park_id === parkId)
      if (!current) return { status: 404, json: { detail: 'inventory_count_not_found' } }
      const body: { lines: InventoryCountLineInput[] } = await request.json()
      const updated: InventoryCount = { ...current, lines: current.lines.map(line => {
        const input = body.lines.find(item => item.catalog_part_id === line.catalog_part_id)
        if (!input) return line
        return { ...line, actual_quantity: input.actual_quantity, difference: String(BigInt(input.actual_quantity) - BigInt(line.expected_quantity)) as `${bigint}`, comment: input.comment ?? null }
      }) }
      counts[counts.indexOf(current)] = updated
      return { json: updated }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/counts\/\d+\/post$/, handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const id = Number(new URL(request.url).pathname.split('/').at(-2))
      const current = counts.find(item => item.id === id && item.park_id === parkId)
      if (!current) return { status: 404, json: { detail: 'inventory_count_not_found' } }
      current.lines.forEach(line => {
        if (line.actual_quantity === null) return
        const stock = stockFor(parkId, line.catalog_part_id)
        inventoryStocks.set(`${parkId}:${line.catalog_part_id}`, { ...stock, quantity: BigInt(line.actual_quantity), version: stock.version + 1n })
      })
      const posted = { ...current, status: 'posted' as const, posted_by: user.id, posted_at: FIXED_TIME }
      counts[counts.indexOf(current)] = posted
      return { json: posted }
    } },
    { method: 'POST', path: /^\/api\/inventory\/parks\/\d+\/counts\/\d+\/cancel$/, handler: request => {
      const parkId = requestedPark(request)
      if (!canReadPark(parkId)) return inventoryDenied
      const id = Number(new URL(request.url).pathname.split('/').at(-2))
      const current = counts.find(item => item.id === id && item.park_id === parkId)
      if (!current) return { status: 404, json: { detail: 'inventory_count_not_found' } }
      const cancelled = { ...current, status: 'cancelled' as const }
      counts[counts.indexOf(current)] = cancelled
      return { json: cancelled }
    } },
    { method: 'GET', path: '/api/inventory/export', handler: request => {
      const url = new URL(request.url)
      const allParks = url.searchParams.get('scope') === 'all'
      if (allParks && !['admin', 'royal'].includes(user.role)) return inventoryDenied
      const parkId = Number(url.searchParams.get('park_id') ?? 0)
      if (!allParks && !canReadPark(parkId)) return inventoryDenied
      const format = url.searchParams.get('format')
      return format === 'csv'
        ? { body: 'park,article,quantity\n7,ABC-1,0\n', headers: { 'Content-Type': 'text/csv' } }
        : { body: 'inventory-workbook', headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' } }
    } },
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
