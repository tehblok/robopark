import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { AuthContext } from '../auth-context'
import { ParkScopeContext } from '../app/park/parkScope'
import { makeUser, park } from '../domains/insights/operations.test-support'
import { analyticsFixture } from '../domains/analytics/analytics.test-support'
import { Analytics } from './Analytics'

it('loads the historical endpoint from the analytics page', async () => {
  const user = makeUser({ role: 'operator' })
  const apiClient = { analytics: vi.fn(async () => analyticsFixture()) }
  render(<MemoryRouter initialEntries={['/analytics?park=7']}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser: async () => user, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Analytics apiClient={apiClient} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'Аналитика' })).toBeVisible()
  expect(await screen.findByRole('heading', { name: 'Динамика процесса' })).toBeVisible()
  expect(apiClient.analytics).toHaveBeenCalledWith(7, 7, '1d')
  expect(screen.queryByText('Сейчас по Tracker')).not.toBeInTheDocument()
})
