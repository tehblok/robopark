/* eslint-disable react/only-export-components */
import { Profiler, StrictMode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import type { OperationsOverview, Park, User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InsightsPage } from './InsightsPage'

export const park: Park = { id: 7, name: 'Север', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
export const otherPark: Park = { ...park, id: 8, name: 'Юг', tag: 'south' }
export function makeUser(overrides: Partial<User> = {}): User {
  return { id: 3, username: 'operator', role: 'operator', access_status: 'approved', parks: [park, otherPark], permissions: ['nav.dashboard', 'nav.analytics', 'nav.tasks', 'tracker.read'], ...overrides }
}
export function snapshot(overrides: Partial<OperationsOverview> = {}): OperationsOverview {
  return { park_id: 7, generated_at: '2026-09-03T08:00:00Z', timezone: 'Europe/Moscow', selected_status: 'all', status_options: [{ key: 'all', label: 'Все доступные' }, { key: 'new', label: 'Новые' }, { key: 'moving', label: 'Перемещение' }, { key: 'queued', label: 'Очередь' }, { key: 'diagnostics', label: 'Диагностика' }], counts: { new: 4, moving: 2, queued: 1, diagnostics: 1 }, tasks: [{ key: 'RP-1', summary: 'Проверить колесо', status: 'Новый', bucket: 'new', robot: '447', created_at: '2026-09-01T08:00:00Z', hours_created: '48', url: '' }], tasks_total: 1, tasks_truncated: false, flow: { definition_version: 2, window_start: '2026-09-02T20:00:00Z', window_end: '2026-09-03T02:00:00Z', expected_buckets: 3, observed_buckets: 2, complete: false, legacy_buckets: 1, points: [{ bucket_start: '2026-09-02T20:00:00Z', arrived_count: 0, departed_count: 0 }, { bucket_start: '2026-09-03T00:00:00Z', arrived_count: 3, departed_count: 2 }] }, sla: { target_hours: null, evaluated_count: 0, unknown_count: 8, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false }, workload: null, operators: null, ...overrides }
}
export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b })
  return { promise, resolve, reject }
}
function Location() { return <output aria-label="URL">{useLocation().search}</output> }
type TreeOptions = {
  user?: User
  selectedPark?: Park | null
  client?: { operationsOverview: (parkId: number, days: number, status: string) => Promise<OperationsOverview> }
  refreshUser?: () => Promise<User>
  url?: string
  onRender?: () => void
  mode?: 'overview' | 'analytics'
  strict?: boolean
}

export function tree({ user = makeUser(), selectedPark = park as Park | null, client = { operationsOverview: vi.fn(async () => snapshot()) }, refreshUser = vi.fn(async () => user), url = '/overview?park=7', onRender = () => {}, mode = 'overview', strict = false }: TreeOptions = {}) {
  const content = <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ selectedPark, parkId: selectedPark?.id ?? null, parks: user.parks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Profiler id="insights" onRender={onRender}><InsightsPage apiClient={client} mode={mode} /></Profiler><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
  return strict ? <StrictMode>{content}</StrictMode> : content
}
