import type { Page, Request as PlaywrightRequest } from '@playwright/test'
import type { OperationsOverview, Park, User } from '../../src/api'

export type MockResponse = {
  status?: number
  json?: unknown
  body?: string
  headers?: Record<string, string>
}
export type MockRouteHandler = (request: Request) => MockResponse | Promise<MockResponse>
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

async function toHandlerRequest(request: PlaywrightRequest): Promise<Request> {
  const method = request.method()
  const bytes = method === 'GET' || method === 'HEAD'
    ? null
    : request.postDataBuffer()
  return new Request(request.url(), {
    method,
    headers: await request.allHeaders(),
    body: bytes ? new Uint8Array(bytes) : undefined,
  })
}

function matchesPath(path: string | RegExp, pathname: string): boolean {
  if (typeof path === 'string') return path === pathname
  path.lastIndex = 0
  return path.test(pathname)
}

function fulfill(response: MockResponse) {
  if (response.json !== undefined) {
    return {
      status: response.status,
      headers: response.headers,
      contentType: 'application/json',
      body: JSON.stringify(response.json),
    }
  }
  return {
    status: response.status,
    headers: response.headers,
    body: response.body,
  }
}

export async function installMockApi(page: Page, options: MockApiOptions = {}): Promise<void> {
  const defaultOperations: OperationsOverview = {
    park_id: options.parks?.[0]?.id ?? 7,
    generated_at: '2026-09-02T09:00:00Z',
    timezone: 'Europe/Moscow',
    status_options: [{ key: 'all', label: 'Все доступные' }],
    selected_status: 'all',
    counts: { all: 0 },
    tasks: [],
    tasks_total: 0,
    tasks_truncated: false,
    flow: {
      definition_version: 2,
      window_start: '2026-09-01T09:00:00Z',
      window_end: '2026-09-02T09:00:00Z',
      expected_buckets: 12,
      observed_buckets: 0,
      complete: false,
      legacy_buckets: 0,
      points: [],
    },
    sla: { target_hours: null, evaluated_count: 0, unknown_count: 0, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false },
    workload: null,
    operators: null,
  }
  const defaults: Record<string, MockResponse> = {
    '/api/auth/me': options.user
      ? { json: options.user }
      : { status: 401, json: { detail: 'Unauthorized' } },
    '/api/ops/maintenance': { json: { active: false, kind: null, operator: false } },
    '/api/reports/badge': { json: { count: 0 } },
    '/api/parks': { json: options.parks ?? [] },
    '/api/dashboard/summary': {
      json: { park_id: 7, arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [] },
    },
    '/api/dashboard/history': { json: { park_id: 7, points: [] } },
    '/api/operations/overview': { json: defaultOperations },
  }

  await page.route(/^https?:\/\/[^/]+\/api(?:\/|$)/, async (route) => {
    const playwrightRequest = route.request()
    const pathname = new URL(playwrightRequest.url()).pathname
    const custom = options.routes?.find((candidate) => (
      candidate.method === playwrightRequest.method()
      && matchesPath(candidate.path, pathname)
    ))
    const response = custom
      ? await custom.handler(await toHandlerRequest(playwrightRequest))
      : defaults[pathname] ?? { status: 404, json: { detail: 'mock_not_configured' } }

    await route.fulfill(fulfill(response))
  })
}
