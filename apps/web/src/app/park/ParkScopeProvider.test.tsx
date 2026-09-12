import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  MemoryRouter,
  useLocation,
  useNavigationType,
  useNavigate,
} from 'react-router-dom'
import { useContext, useState } from 'react'
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

type ScopeSnapshot = {
  userId: number
  parkId: number | null
  parks: number[]
  loading: boolean
}

function RecordingProbe({ snapshots }: { snapshots: ScopeSnapshot[] }) {
  const scope = useParkScope()
  const { user } = useContext(AuthContext)!
  const location = useLocation()
  snapshots.push({
    userId: user!.id,
    parkId: scope.parkId,
    parks: scope.parks.map((item) => item.id),
    loading: scope.loading,
  })
  return (
    <>
      <output data-testid="park-id">{scope.parkId ?? 'none'}</output>
      <output data-testid="parks">{scope.parks.map((item) => item.id).join(',')}</output>
      <output data-testid="loading">{String(scope.loading)}</output>
      <output data-testid="location">{location.search}</output>
      <button type="button" onClick={() => void scope.refreshParks()}>
        Обновить парки
      </button>
    </>
  )
}

function SwitchingScope({
  initialUser,
  nextUser,
  nextPath,
  snapshots,
}: {
  initialUser: User
  nextUser: User
  nextPath?: string
  snapshots: ScopeSnapshot[]
}) {
  const [user, setUser] = useState(initialUser)
  const navigate = useNavigate()
  return (
    <AuthContext.Provider
      value={{
        user,
        loading: false,
        login: vi.fn(),
        refreshUser: vi.fn<() => Promise<User>>().mockResolvedValue(user),
        logout: vi.fn(),
      }}
    >
      <button
        type="button"
        onClick={() => {
          setUser(nextUser)
          if (nextPath) navigate(nextPath)
        }}
      >
        Сменить пользователя
      </button>
      <ParkScopeProvider>
        <RecordingProbe snapshots={snapshots} />
      </ParkScopeProvider>
    </AuthContext.Provider>
  )
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
      <output data-testid="allow-all">{String(scope.allowAllParks)}</output>
      <output data-testid="location">{location.search}</output>
      <output data-testid="navigation-type">{navigationType}</output>
      <button type="button" onClick={() => scope.setParkId(9)}>
        Выбрать парк 9
      </button>
      <button type="button" onClick={() => scope.setParkId(null)}>Все доступные</button>
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
  it.each(['admin', 'royal'] as const)('loads active and inactive fleet parks for %s inventory export', async role => {
    vi.spyOn(api, 'parks').mockResolvedValue([park(7), { ...park(9), is_active: false }])

    renderScope('/inventory?view=export&park=9', { ...scopeUser('operator', []), role })

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    expect(screen.getByTestId('parks')).toHaveTextContent('7,9')
    expect(screen.getByTestId('location')).toHaveTextContent('view=export&park=9')
  })

  it('loads only active fleet parks for operator inventory selection', async () => {
    vi.spyOn(api, 'parks').mockResolvedValue([park(7), { ...park(9), is_active: false }])

    renderScope('/inventory?view=export&park=9', scopeUser('operator', [park(7)]))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('view=export&park=7'))
    expect(screen.getByTestId('parks')).toHaveTextContent('7')
    expect(screen.getByTestId('park-id')).toHaveTextContent('7')
  })

  it.each(['admin', 'royal'] as const)('drops an inactive %s selection when leaving inventory export', async role => {
    const principal = { ...scopeUser('operator', []), role }
    vi.spyOn(api, 'parks').mockResolvedValue([park(7), { ...park(9), is_active: false }])
    const snapshots: ScopeSnapshot[] = []
    const actor = userEvent.setup()

    render(<MemoryRouter initialEntries={['/inventory?view=export&park=9']}><SwitchingScope
      initialUser={principal} nextPath="/inventory?view=parts&park=9" nextUser={principal} snapshots={snapshots}
    /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))

    await actor.click(screen.getByRole('button', { name: 'Сменить пользователя' }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('view=parts&park=7'))
    expect(screen.getByTestId('parks')).toHaveTextContent('7')
    expect(screen.getByTestId('park-id')).toHaveTextContent('7')
  })

  it('keeps a mechanic restricted to assigned parks on inventory', async () => {
    const parksRequest = vi.spyOn(api, 'parks')

    renderScope('/inventory?view=export&park=9', scopeUser('mechanic', [park(7), park(9)]))

    expect(await screen.findByTestId('park-id')).toHaveTextContent('9')
    expect(screen.getByTestId('parks')).toHaveTextContent('7,9')
    expect(parksRequest).not.toHaveBeenCalled()
  })

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

  it('defaults overview to all available parks instead of the stored work park', async () => {
    sessionStorage.setItem(PARK_STORAGE_KEY, '9')

    renderScope('/overview?tab=alerts', scopeUser('operator', [park(7), park(9)]))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?tab=alerts&park=all'))
    expect(screen.getByTestId('park-id')).toHaveTextContent('none')
    expect(screen.getByTestId('allow-all')).toHaveTextContent('true')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('9')
    expect(screen.getByTestId('navigation-type')).toHaveTextContent('REPLACE')
  })

  it.each(['admin', 'royal', 'operator'])('supports all/single on analytics for %s without expanding access', async role => {
    vi.spyOn(api, 'parks').mockResolvedValue([park(7), park(9), { ...park(10), is_active: false }])
    const principal = { ...scopeUser('operator', [park(9)]), role }
    const { actor } = renderScope('/analytics?period=30', principal)
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('park=all'))
    expect(screen.getByTestId('parks')).toHaveTextContent(role === 'operator' ? '9' : '7,9')
    await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))
    expect(screen.getByTestId('selected-park')).toHaveTextContent('Парк 9')
    await actor.click(screen.getByRole('button', { name: 'Все доступные' }))
    expect(screen.getByTestId('location')).toHaveTextContent('period=30&park=all')
    expect(screen.getByTestId('park-id')).toHaveTextContent('none')
  })

  it.each(['/work?park=all', '/robots?park=all'])('restores the last individual work park outside insights at %s', async path => {
    sessionStorage.setItem(PARK_STORAGE_KEY, '9')
    renderScope(path, scopeUser('operator', [park(7), park(9)]))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('park=9'))
    expect(screen.getByTestId('allow-all')).toHaveTextContent('false')
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

    await waitFor(() => expect(screen.getByTestId('loading')).toHaveTextContent('false'))
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
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('?park=8')
    })
    expect(refreshUser).toHaveBeenCalledTimes(1)
    expect(parksRequest).not.toHaveBeenCalled()
  })

  it('preserves a valid fleet URL without publishing the previous assigned scope', async () => {
    const fleetRequest = deferred<Park[]>()
    vi.spyOn(api, 'parks').mockReturnValue(fleetRequest.promise)
    const snapshots: ScopeSnapshot[] = []
    const actor = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/overview?park=7']}>
        <SwitchingScope
          initialUser={scopeUser('operator', [park(7)])}
          nextUser={customUser(['parks.manage'], [])}
          nextPath="/overview?park=9"
          snapshots={snapshots}
        />
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('park-id')).toHaveTextContent('7')
    snapshots.length = 0

    await actor.click(screen.getByRole('button', { name: 'Сменить пользователя' }))

    expect(screen.getByTestId('location')).toHaveTextContent('?park=9')
    expect(screen.getByTestId('parks')).toBeEmptyDOMElement()
    expect(screen.getByTestId('park-id')).toHaveTextContent('none')
    expect(screen.getByTestId('loading')).toHaveTextContent('true')
    expect(
      snapshots
        .filter((snapshot) => snapshot.userId === 2)
        .every((snapshot) => snapshot.parks.length === 0 && snapshot.parkId == null),
    ).toBe(true)

    fleetRequest.resolve([park(7), park(9)])

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    expect(screen.getByTestId('location')).toHaveTextContent('?park=9')
  })

  it('publishes assigned parks synchronously when fleet scope becomes non-fleet', async () => {
    const fleetRequest = deferred<Park[]>()
    vi.spyOn(api, 'parks').mockReturnValue(fleetRequest.promise)
    const snapshots: ScopeSnapshot[] = []
    const actor = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/overview?park=9']}>
        <SwitchingScope
          initialUser={customUser(['parks.manage'], [])}
          nextUser={scopeUser('operator', [park(8)])}
          snapshots={snapshots}
        />
      </MemoryRouter>,
    )
    fleetRequest.resolve([park(7), park(9)])
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    snapshots.length = 0

    await actor.click(screen.getByRole('button', { name: 'Сменить пользователя' }))

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('8'))
    expect(screen.getByTestId('parks')).toHaveTextContent('8')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=8')
    expect(
      snapshots
        .filter((snapshot) => snapshot.userId === 1)
        .every(
          (snapshot) =>
            snapshot.parks.join(',') === '8'
            && (snapshot.parkId == null || snapshot.parkId === 8),
        ),
    ).toBe(true)
  })

  it('keeps the initial fleet request from committing after a refresh starts', async () => {
    const initialRequest = deferred<Park[]>()
    const refreshRequest = deferred<Park[]>()
    const parksRequest = vi
      .spyOn(api, 'parks')
      .mockReturnValueOnce(initialRequest.promise)
      .mockReturnValueOnce(refreshRequest.promise)
    const { actor } = renderScope(
      '/overview?park=9',
      customUser(['parks.manage'], []),
    )
    await waitFor(() => expect(parksRequest).toHaveBeenCalledTimes(1))

    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))
    expect(parksRequest).toHaveBeenCalledTimes(2)

    await act(async () => {
      initialRequest.resolve([park(7)])
      await initialRequest.promise
    })

    expect(screen.getByTestId('loading')).toHaveTextContent('true')
    expect(screen.getByTestId('parks')).toBeEmptyDOMElement()
    expect(screen.getByTestId('location')).toHaveTextContent('?park=9')

    await act(async () => {
      refreshRequest.resolve([park(9)])
      await refreshRequest.promise
    })

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
  })

  it('keeps loading and data owned by the latest fleet refresh', async () => {
    const olderRefresh = deferred<Park[]>()
    const latestRefresh = deferred<Park[]>()
    const parksRequest = vi
      .spyOn(api, 'parks')
      .mockResolvedValueOnce([park(7)])
      .mockReturnValueOnce(olderRefresh.promise)
      .mockReturnValueOnce(latestRefresh.promise)
    const { actor } = renderScope(
      '/overview?park=7',
      customUser(['parks.manage'], []),
    )
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('7'))

    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))
    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))
    expect(parksRequest).toHaveBeenCalledTimes(3)

    await act(async () => {
      olderRefresh.resolve([park(8)])
      await olderRefresh.promise
    })

    expect(screen.getByTestId('loading')).toHaveTextContent('true')
    expect(screen.getByTestId('parks')).toHaveTextContent('7')
    expect(screen.getByTestId('park-id')).toHaveTextContent('7')

    await act(async () => {
      latestRefresh.resolve([park(9)])
      await latestRefresh.promise
    })

    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('9'))
    expect(screen.getByTestId('parks')).toHaveTextContent('9')
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
  })

  it('ignores a fleet refresh that completes after the user becomes non-fleet', async () => {
    const staleRefresh = deferred<Park[]>()
    const parksRequest = vi
      .spyOn(api, 'parks')
      .mockResolvedValueOnce([park(7)])
      .mockReturnValueOnce(staleRefresh.promise)
    const snapshots: ScopeSnapshot[] = []
    const actor = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/overview?park=7']}>
        <SwitchingScope
          initialUser={customUser(['parks.manage'], [])}
          nextUser={scopeUser('operator', [park(8)])}
          snapshots={snapshots}
        />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('7'))
    await actor.click(screen.getByRole('button', { name: 'Обновить парки' }))
    expect(parksRequest).toHaveBeenCalledTimes(2)

    await actor.click(screen.getByRole('button', { name: 'Сменить пользователя' }))
    await waitFor(() => expect(screen.getByTestId('park-id')).toHaveTextContent('8'))
    expect(screen.getByTestId('loading')).toHaveTextContent('false')

    await act(async () => {
      staleRefresh.resolve([park(9)])
      await staleRefresh.promise
    })

    expect(screen.getByTestId('parks')).toHaveTextContent('8')
    expect(screen.getByTestId('park-id')).toHaveTextContent('8')
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=8')
  })
})
