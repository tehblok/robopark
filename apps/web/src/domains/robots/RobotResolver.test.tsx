import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext, type ParkScopeValue } from '../../app/park/parkScope'
import { RobotResolver } from './RobotResolver'
import { RobotsPage } from './RobotsPage'
import { loadRecentRobots } from './recentRobots'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

const user = (id = 7): User => ({
  id,
  username: `user-${id}`,
  role: 'operator',
  access_status: 'approved',
  permissions: [],
  parks: [],
})

const parkScope: ParkScopeValue = {
  parkId: 7,
  selectedPark: null,
  parks: [],
  loading: false,
  locked: false,
  setParkId: () => undefined,
  refreshParks: async () => undefined,
}

function robotsPageTree({
  currentUser = user(),
  apiClient,
  path = '/robots?park=7',
}: {
  currentUser?: User
  apiClient: { emergencyResolve: (reference: string) => Promise<{ vin: string, sections: [] }> }
  path?: string
}) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={{
        user: currentUser,
        loading: false,
        login: async () => currentUser,
        refreshUser: async () => currentUser,
        logout: async () => undefined,
      }}>
        <ParkScopeContext.Provider value={parkScope}>
          <RobotsPage apiClient={apiClient} />
          <LocationProbe />
        </ParkScopeContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

describe('RobotResolver', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('normalizes a pasted deep link and reports a resolved VIN', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-02T09:00:00Z'))
    const emergencyResolve = vi.fn(async () => ({ vin: 'YASADR00000001975', sections: [] as [] }))
    const onResolved = vi.fn()
    const onValueChange = vi.fn()
    render(
      <RobotResolver
        apiClient={{ emergencyResolve }}
        onResolved={onResolved}
        onValueChange={onValueChange}
        userId={7}
        value="https://robopark.example/emergency?q=1975&tab=map"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
    await waitFor(() => expect(emergencyResolve).toHaveBeenCalledWith('1975'))
    expect(onResolved).toHaveBeenCalledWith({ vin: 'YASADR00000001975', sections: [] })
    expect(loadRecentRobots(7, Date.now())).toEqual(expect.arrayContaining([
      expect.objectContaining({ vin: 'YASADR00000001975' }),
    ]))
  })

  it('rejects invalid input without a network request', () => {
    const emergencyResolve = vi.fn()
    render(
      <RobotResolver
        apiClient={{ emergencyResolve }}
        onResolved={vi.fn()}
        onValueChange={vi.fn()}
        userId={7}
        value="not a robot"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
    expect(emergencyResolve).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Введите номер, VIN или ссылку на робота')
  })

  it('does not publish a late resolution after its principal changes', async () => {
    let resolve!: (result: { vin: string, sections: [] }) => void
    const emergencyResolve = vi.fn(() => new Promise<{ vin: string, sections: [] }>((done) => {
      resolve = done
    }))
    const onResolved = vi.fn()
    const props = {
      apiClient: { emergencyResolve },
      onResolved,
      onValueChange: vi.fn(),
      value: '447',
    }
    const view = render(<RobotResolver {...props} userId={7} />)
    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
    view.rerender(<RobotResolver {...props} userId={8} />)
    resolve({ vin: 'YASADR00000000447', sections: [] })

    await waitFor(() => expect(emergencyResolve).toHaveBeenCalledTimes(1))
    expect(onResolved).not.toHaveBeenCalled()
    expect(loadRecentRobots(7)).toEqual([])
    expect(loadRecentRobots(8)).toEqual([])
  })

  it('keeps manual input visible when scanning is unsupported', () => {
    render(<RobotResolver apiClient={{ emergencyResolve: vi.fn() }} onResolved={vi.fn()} onValueChange={vi.fn()} userId={7} value="" />)
    expect(screen.getByLabelText('Номер или VIN робота')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Сканировать' })).not.toBeInTheDocument()
  })

  it('replaces q while retaining numeric park and pushes the canonical resolved VIN', async () => {
    const emergencyResolve = vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [] as [] }))
    render(robotsPageTree({ apiClient: { emergencyResolve } }))

    fireEvent.change(screen.getByLabelText('Номер или VIN робота'), { target: { value: '447' } })
    expect(screen.getByTestId('location')).toHaveTextContent('/robots?park=7&q=447')
    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/robots/YASADR00000000447?park=7'))
  })

  it('does not show another principal’s recents and clears only the active user', async () => {
    const emergencyResolve = vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [] as [] }))
    const view = render(robotsPageTree({ apiClient: { emergencyResolve } }))
    fireEvent.change(screen.getByLabelText('Номер или VIN робота'), { target: { value: '447' } })
    fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
    await waitFor(() => expect(loadRecentRobots(7)).toHaveLength(1))

    view.rerender(robotsPageTree({ apiClient: { emergencyResolve }, currentUser: user(8) }))
    expect(screen.queryByText('YASADR00000000447')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Очистить' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }))
    expect(loadRecentRobots(7)).toHaveLength(1)
    expect(loadRecentRobots(8)).toEqual([])
  })
})
