import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { type DashboardSummary, type User } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import { WorkPage } from './WorkPage'
import type { IssueWorkbenchApiClient } from './IssueWorkbench'

const park = { id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
const user: User = {
  id: 3, username: 'operator', role: 'operator', access_status: 'approved',
  permissions: ['nav.tasks', 'tracker.read'], parks: [park],
}
const summary: DashboardSummary = {
  park_id: 7, generated_at: '2026-09-15T09:00:00Z', arrived: 4, done: 3,
  queued: 2, in_transit: 1, moving: [],
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderPage(dashboardSummary = vi.fn(async () => summary), currentUser = user, url = '/work?park=7') {
  const apiClient = {
    trackerIssues: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0, has_more: false })),
    dashboardSummary,
  } as unknown as IssueWorkbenchApiClient & { dashboardSummary: typeof dashboardSummary }
  render(
    <MemoryRouter initialEntries={[url]}>
      <AuthContext.Provider value={{ user: currentUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>
        <ParkScopeContext.Provider value={{
          parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false,
          allowAllParks: false, setParkId: vi.fn(), refreshParks: vi.fn(),
        }}>
          <Routes>
            <Route path="/work" element={<WorkPage apiClient={apiClient} />} />
            <Route path="/work/:issueKey" element={<WorkPage apiClient={apiClient} />} />
          </Routes>
          <Location />
        </ParkScopeContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
  return { apiClient, dashboardSummary }
}

describe('WorkPage operations summary', () => {
  it('marks a selected task so its mobile layout can lead with task content', () => {
    renderPage(undefined, user, '/work/ROBOPARK-42?park=7')

    expect(document.querySelector('.rp-page-layout')).toHaveClass('rp-work-page--detail')
  })

  it('does not request dashboard data while the compact summary is collapsed', async () => {
    const { dashboardSummary } = renderPage()
    expect(await screen.findByRole('heading', { name: 'Работа' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Сводка смены' })).toHaveAttribute('aria-expanded', 'false')
    expect(document.querySelector('.rp-work-summary')).toHaveAttribute('data-open', 'false')
    expect(dashboardSummary).not.toHaveBeenCalled()
  })

  it('loads the cached local summary endpoint only after opening', async () => {
    const { dashboardSummary } = renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Сводка смены' }))
    expect(document.querySelector('.rp-work-summary')).toHaveAttribute('data-open', 'true')
    await waitFor(() => expect(dashboardSummary).toHaveBeenCalledExactlyOnceWith(7))
    expect(screen.getByText('Пришли: 4')).toBeVisible()
    expect(screen.getByText('Ушли: 3')).toBeVisible()
    expect(screen.getByText('В очереди: 2')).toBeVisible()
    expect(screen.getByText('В пути: 1')).toBeVisible()
  })

  it('defaults mechanics to the queued work view', async () => {
    const mechanic = { ...user, username: 'mechanic', role: 'mechanic' as const }
    const { apiClient } = renderPage(undefined, mechanic)
    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'queued' }),
    ))
  })

  it('lets a mechanic switch from the priority queue to other open statuses', async () => {
    const mechanic = { ...user, username: 'mechanic', role: 'mechanic' as const }
    const { apiClient } = renderPage(undefined, mechanic)
    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ status: 'queued' })))
    fireEvent.change(screen.getByRole('combobox', { name: 'Статус задач' }), { target: { value: 'diagnostics' } })
    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ status: 'diagnostics' })))
    fireEvent.change(screen.getByRole('combobox', { name: 'Статус задач' }), { target: { value: 'all' } })
    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ status: undefined, open_only: true, sort: 'queue_first' })))
  })

  it('defaults drivers to an API-permitted viewing status', async () => {
    const driver = { ...user, username: 'driver', role: 'driver' as const }
    const { apiClient } = renderPage(undefined, driver)

    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'new', open_only: true }),
    ))
    expect(screen.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('new')
  })

  it('keeps a driver overview link scoped to all driver-visible open statuses', async () => {
    const driver = { ...user, username: 'driver', role: 'driver' as const }
    const { apiClient } = renderPage(undefined, driver, '/work?park=7&status=all')

    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(
      expect.objectContaining({ status: undefined, open_only: true }),
    ))
    expect(screen.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('all')
    expect(screen.getByTestId('location')).toHaveTextContent('/work?park=7&status=all')
  })

  it('does not request a forbidden driver status from a stale deep link', async () => {
    const driver = { ...user, username: 'driver', role: 'driver' as const }
    const { apiClient } = renderPage(undefined, driver, '/work?park=7&status=queued&robot=447')

    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'new', open_only: true, robot: '447' }),
    ))
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['all', 'new', 'moving'])
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/work?park=7&status=new&robot=447'))
  })
})
