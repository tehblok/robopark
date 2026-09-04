import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { makeUser, park, snapshot } from '../insights/operations.test-support'
import { OverviewPage } from './OverviewPage'

it('uses the shared operations request and chart on Overview', async () => {
  const user = makeUser({ role: 'operator' })
  const apiClient = { operationsOverview: vi.fn(async () => snapshot()) }
  render(<MemoryRouter initialEntries={['/overview?park=7']}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser: async () => user, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><OverviewPage apiClient={apiClient} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  expect(await screen.findByText('Проверить колесо')).toBeVisible()
  expect(apiClient.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
})
