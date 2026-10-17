import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import type { RobotRegistry, User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import * as deviceResourceCache from '../../lib/deviceResourceCache'
import { RobotsPage } from './RobotsPage'

const park = { id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'Alpha', tracker_queue: 'ROBOPARK' }
const user: User = {
  id: 11,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.robot_search', 'tracker.read'],
  parks: [park],
}
const preparedRegistry: RobotRegistry = {
  items: [{
    vin: 'YASADR00000001170',
    short_number: '1170',
    park_ids: [park.id],
    task_keys: ['ROBOPARK-1'],
    issue_keys: ['ROBOPARK-1'],
    task_count: 1,
    error_count: null,
    telemetry: null,
    state: 'unknown',
  }],
  total: 1,
  offset: 0,
  limit: 100,
  has_more: false,
  partial: false,
  source_complete: true,
  source: 'scoped_tracker_issues',
  park_id: park.id,
}

afterEach(() => vi.restoreAllMocks())

it('keeps direct search offline without restoring the removed registry', async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  vi.spyOn(deviceResourceCache, 'currentDeviceResourceCache').mockReturnValue({
    get: vi.fn(async () => preparedRegistry),
  } as unknown as ReturnType<typeof deviceResourceCache.currentDeviceResourceCache>)
  const apiClient = {
    emergencyResolve: vi.fn(),
    robotRegistry: vi.fn(),
  }

  render(
    <MemoryRouter initialEntries={['/robots?park=7']}>
      <AuthContext.Provider value={{
        user,
        loading: false,
        login: async () => user,
        refreshUser: async () => user,
        logout: async () => undefined,
      }}>
        <ParkScopeContext.Provider value={{
          parkId: park.id,
          selectedPark: park,
          parks: [park],
          loading: false,
          locked: false,
          setParkId: vi.fn(),
          refreshParks: async () => undefined,
        }}>
          <RobotsPage apiClient={apiClient} />
        </ParkScopeContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )

  expect(screen.queryByRole('heading', { name: 'Роботы в работе' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Открыть робота 1170' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Номер или VIN робота')).toBeVisible()
  expect(apiClient.robotRegistry).not.toHaveBeenCalled()
})
