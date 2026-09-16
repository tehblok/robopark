import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { type DashboardSummary, type User } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import { WorkPage } from './WorkPage'
import type { IssueWorkbenchApiClient } from './IssueWorkbench'

const park = { id: 7, name: 'Север', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
const user: User = {
  id: 3, username: 'operator', role: 'operator', access_status: 'approved',
  permissions: ['nav.tasks', 'tracker.read'], parks: [park],
}
const summary: DashboardSummary = {
  park_id: 7, generated_at: '2026-09-15T09:00:00Z', arrived: 4, done: 3,
  queued: 2, in_transit: 1, moving: [],
}

function renderPage(dashboardSummary = vi.fn(async () => summary), currentUser = user) {
  const apiClient = {
    trackerIssues: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0, has_more: false })),
    dashboardSummary,
  } as unknown as IssueWorkbenchApiClient & { dashboardSummary: typeof dashboardSummary }
  render(
    <MemoryRouter initialEntries={['/work?park=7']}>
      <AuthContext.Provider value={{ user: currentUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>
        <ParkScopeContext.Provider value={{
          parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false,
          allowAllParks: false, setParkId: vi.fn(), refreshParks: vi.fn(),
        }}>
          <Routes><Route path="/work" element={<WorkPage apiClient={apiClient} />} /></Routes>
        </ParkScopeContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
  return { apiClient, dashboardSummary }
}

describe('WorkPage operations summary', () => {
  it('does not request dashboard data while the compact summary is collapsed', async () => {
    const { dashboardSummary } = renderPage()
    expect(await screen.findByRole('heading', { name: 'Работа' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Сводка смены' })).toHaveAttribute('aria-expanded', 'false')
    expect(dashboardSummary).not.toHaveBeenCalled()
  })

  it('loads the cached local summary endpoint only after opening', async () => {
    const { dashboardSummary } = renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Сводка смены' }))
    await waitFor(() => expect(dashboardSummary).toHaveBeenCalledExactlyOnceWith(7))
    expect(screen.getByText('Пришли: 4')).toBeVisible()
    expect(screen.getByText('Ушли: 3')).toBeVisible()
    expect(screen.getByText('В очереди: 2')).toBeVisible()
    expect(screen.getByText('В пути: 1')).toBeVisible()
  })

  it('defaults every role to the queued work view', async () => {
    const mechanic = { ...user, username: 'mechanic', role: 'mechanic' as const }
    const { apiClient } = renderPage(undefined, mechanic)
    await waitFor(() => expect(apiClient.trackerIssues).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'queued' }),
    ))
  })
})
