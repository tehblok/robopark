import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import type { OperationsOverview } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { OverviewPage } from './OverviewPage'

it('driver receives the operations task queue and honest unconfigured SLA', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: false, media: '(max-width: 599px)', onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  }))
  const park = { id: 7, name: 'Север', tag: 'north', tracker_queue: 'ROBOPARK' }
  const user = { id: 1, username: 'driver', role: 'driver', access_status: 'approved', parks: [park], permissions: ['nav.dashboard', 'nav.tasks', 'tracker.read'] }
  const client = {
    dashboardSummary: vi.fn(), mechanicTasks: vi.fn(), operatorBlockers: vi.fn(), trackerIssues: vi.fn(),
    operationsOverview: vi.fn(async () => ({ park_id: 7, generated_at: '2026-09-03T08:00:00Z', timezone: 'Europe/Moscow', selected_status: 'all', status_options: [{ key: 'all', label: 'Все доступные' }, { key: 'new', label: 'Новые' }, { key: 'moving', label: 'Перемещение' }], counts: { new: 1, moving: 0 }, tasks: [{ key: 'RP-1', summary: 'Новая задача водителя', status: 'Новый', bucket: 'new', robot: null, created_at: null, hours_created: null, url: '' }], tasks_total: 1, tasks_truncated: false, flow: { definition_version: 2, window_start: '2026-09-02T08:00:00Z', window_end: '2026-09-03T08:00:00Z', expected_buckets: 12, observed_buckets: 0, complete: false, legacy_buckets: 0, points: [] }, sla: { target_hours: null, evaluated_count: 0, unknown_count: 1, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false }, workload: null, operators: null } satisfies OperationsOverview)),
  }
  render(<MemoryRouter><AuthContext.Provider value={{ user, loading: false, login: async () => user, logout: async () => {}, refreshUser: async () => user }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><OverviewPage apiClient={client} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  expect(await screen.findByText('Новая задача водителя')).toBeVisible()
  expect(screen.getByText('Норматив SLA не задан')).toBeVisible()
  expect(screen.queryByText('Просрочек нет')).not.toBeInTheDocument()
})
