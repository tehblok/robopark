import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { AuthContext } from '../auth-context'
import { ParkScopeContext } from '../app/park/parkScope'
import { makeUser, park, snapshot } from '../domains/insights/operations.test-support'
import { Analytics } from './Analytics'

it('uses the shared operations view for operator analytics', async () => {
  const user = makeUser({ role: 'operator' })
  const apiClient = { operationsOverview: vi.fn(async () => snapshot()) }
  render(<MemoryRouter initialEntries={['/analytics?park=7']}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser: async () => user, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Analytics apiClient={apiClient} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'Аналитика' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  expect(screen.queryByText('Сейчас по Tracker')).not.toBeInTheDocument()
})
