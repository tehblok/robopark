import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  MemoryRouter,
  useLocation,
  useNavigationType,
} from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Park, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from './ParkScopeProvider'
import { PARK_STORAGE_KEY, useParkScope } from './parkScope'

function park(id: number): Park {
  return { id, name: `Парк ${id}`, tag: `park-${id}`, is_active: true }
}

function scopeUser(role: 'operator' | 'mechanic', parks: Park[]): User {
  return {
    id: 1,
    username: `${role}-scope`,
    role,
    access_status: 'approved',
    permissions: ['nav.dashboard'],
    parks,
  }
}

function customUser(
  permissions: string[],
  parks: Park[],
  role = 'field_lead',
): User {
  return {
    id: 2,
    username: `${role}-scope`,
    role,
    access_status: 'approved',
    permissions,
    parks,
  }
}

function Probe() {
  const scope = useParkScope()
  const location = useLocation()
  const navigationType = useNavigationType()
  return (
    <>
      <output data-testid="park-id">{scope.parkId ?? 'none'}</output>
      <output data-testid="selected-park">{scope.selectedPark?.name ?? 'none'}</output>
      <output data-testid="parks">{scope.parks.map((item) => item.id).join(',')}</output>
      <output data-testid="loading">{String(scope.loading)}</output>
      <output data-testid="locked">{String(scope.locked)}</output>
      <output data-testid="location">{location.search}</output>
      <output data-testid="navigation-type">{navigationType}</output>
      <button type="button" onClick={() => scope.setParkId(9)}>
        Выбрать парк 9
      </button>
      <button type="button" onClick={() => void scope.refreshParks()}>
        Обновить парки
      </button>
    </>
  )
}

function renderScope(
  path: string,
  currentUser: User,
  refreshUser = vi.fn<() => Promise<User>>().mockResolvedValue(currentUser),
) {
  const actor = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider
        value={{
          user: currentUser,
          loading: false,
          login: vi.fn(),
          refreshUser,
          logout: vi.fn(),
        }}
      >
        <ParkScopeProvider>
          <Probe />
        </ParkScopeProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
  return { actor, refreshUser }
}

afterEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('ParkScopeProvider', () => {
  it('prefers a valid park from the URL over session storage', async () => {
    sessionStorage.setItem(PARK_STORAGE_KEY, '7')

    renderScope('/overview?park=9', scopeUser('operator', [park(7), park(9)]))

    expect(await screen.findByTestId('park-id')).toHaveTextContent('9')
    expect(screen.getByTestId('selected-park')).toHaveTextContent('Парк 9')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('9')
  })

  it('removes an unknown park, preserves other query parameters, and falls back to the first assigned park', async () => {
    renderScope(
      '/overview?tab=alerts&park=404',
      scopeUser('operator', [park(7), park(9)]),
    )

    expect(await screen.findByTestId('park-id')).toHaveTextContent('7')
    expect(screen.getByTestId('location')).toHaveTextContent('?tab=alerts&park=7')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('7')
    expect(screen.getByTestId('navigation-type')).toHaveTextContent('REPLACE')
  })

  it('uses a valid stored park when the URL has no park', async () => {
    sessionStorage.setItem(PARK_STORAGE_KEY, '9')

    renderScope('/overview?tab=alerts', scopeUser('operator', [park(7), park(9)]))

    expect(await screen.findByTestId('park-id')).toHaveTextContent('9')
    expect(screen.getByTestId('location')).toHaveTextContent('?tab=alerts&park=9')
    expect(screen.getByTestId('navigation-type')).toHaveTextContent('REPLACE')
  })

  it('clears an unavailable park when the actor has no permitted parks', async () => {
    sessionStorage.setItem(PARK_STORAGE_KEY, '7')

    renderScope('/overview?tab=alerts&park=7', scopeUser('operator', []))

    expect(await screen.findByTestId('park-id')).toHaveTextContent('none')
    expect(screen.getByTestId('selected-park')).toHaveTextContent('none')
    expect(screen.getByTestId('location')).toHaveTextContent('?tab=alerts')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBeNull()
  })

  it('does not let a single-park mechanic change scope', async () => {
    const { actor } = renderScope(
      '/overview?park=7',
      scopeUser('mechanic', [park(7)]),
    )

    expect(await screen.findByTestId('locked')).toHaveTextContent('true')
    await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))

    expect(screen.getByTestId('park-id')).toHaveTextContent('7')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=7')
  })

  it('uses parks.manage to load, select, and refresh fleet parks for a custom role', async () => {
    const parksRequest = vi
      .spyOn(api, 'parks')
      .mockResolvedValue([park(7), park(9), { ...park(11), is_active: false }])
    const { actor } = renderScope(
      '/overview?view=compact',
      customUser(['nav.dashboard', 'parks.manage'], []),
    )

    expect(await screen.findByTestId('loading')).toHaveTextContent('false')
    expect(screen.getByTestId('parks')).toHaveTextContent('7,9')
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('7'))
    expect(parksRequest).toHaveBeenCalledTimes(1)

    await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))

    expect(screen.getByTestId('park-id')).toHaveTextContent('9')
    expect(screen.getByTestId('selected-park')).toHaveTextContent('Парк 9')
    expect(screen.getByTestId('location')).toHaveTextContent('?view=compact&park=9')
    expect(screen.getByTestId('navigation-type')).toHaveTextContent('PUSH')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('9')

    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))

    await waitFor(() => expect(parksRequest).toHaveBeenCalledTimes(2))
  })

  it('keeps a valid fleet URL selection while the available parks load', async () => {
    vi.spyOn(api, 'parks').mockResolvedValue([park(7), park(9)])

    renderScope(
      '/overview?view=compact&park=9',
      customUser(['nav.dashboard', 'parks.manage'], []),
    )

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    expect(screen.getByTestId('location')).toHaveTextContent('?view=compact&park=9')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('9')
  })

  it('keeps a custom role without parks.manage assigned-park scoped during selection and refresh', async () => {
    const parksRequest = vi.spyOn(api, 'parks')
    const refreshed = customUser(['nav.dashboard', 'users.manage'], [park(8)])
    const refreshUser = vi.fn<() => Promise<User>>().mockResolvedValue(refreshed)
    const { actor } = renderScope(
      '/overview?park=7',
      customUser(['nav.dashboard', 'users.manage'], [park(7)]),
      refreshUser,
    )

    expect(await screen.findByTestId('park-id')).toHaveTextContent('7')
    await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))

    expect(screen.getByTestId('park-id')).toHaveTextContent('7')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=7')
    expect(parksRequest).not.toHaveBeenCalled()

    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))

    expect(await screen.findByTestId('parks')).toHaveTextContent('8')
    expect(screen.getByTestId('park-id')).toHaveTextContent('8')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=8')
    expect(refreshUser).toHaveBeenCalledTimes(1)
    expect(parksRequest).not.toHaveBeenCalled()
  })
})
