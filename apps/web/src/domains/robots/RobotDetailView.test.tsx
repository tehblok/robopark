import { Profiler, StrictMode, useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type Blocker, type EmergencySnapshot, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { resourceStore } from '../../lib/resource'
import { loadRecentRobots } from './recentRobots'
import type { RobotDetailApiClient } from './robotDetailData'
import { RobotDetailView } from './RobotDetailView'
import { RobotPage } from './RobotPage'

const VIN = 'YASADR00000000447'
const park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK', is_active: true }
const work: Blocker = { key: 'ROBOPARK-42', summary: 'Робот остановился', status: 'Open', status_key: 'open', robot: '447', created_at: '2026-09-02T08:00:00Z', hours_created: '1', url: 'https://st.yandex-team.ru/ROBOPARK-42', bucket: 'new' }
function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: VIN, short_number: '447', observed_at: new Date().toISOString(), online: true,
    speed: 0, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
    mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true,
    connection: 'lte', error_banner: null, lat: null, lon: null, heading_deg: null, wheels_fault: [], ...overrides,
  }
}
function user(overrides: Partial<User> = {}): User {
  return { id: 3, username: 'operator', role: 'operator', access_status: 'approved', permissions: ['nav.emergency', 'nav.robot_search', 'nav.tasks', 'tracker.read'], parks: [park], ...overrides }
}
function client(overrides: Partial<RobotDetailApiClient> = {}): RobotDetailApiClient {
  return {
    emergencyResolve: vi.fn(async () => ({ vin: VIN, sections: [] })),
    emergencySnapshot: vi.fn(async () => snapshot()),
    mechanicRobotTickets: vi.fn(async () => ({ query: VIN, items: [work] })),
    operatorRobotTickets: vi.fn(async () => ({ query: VIN, items: [work] })),
    trackerRobotTickets: vi.fn(async () => ({ query: VIN, items: [work] })), ...overrides,
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output aria-label="Адрес">{location.pathname}{location.search}</output><button onClick={() => navigate('/robots/448?park=7')}>Другой робот</button></>
}
function tree(apiClient: RobotDetailApiClient, currentUser: User | null = user(), reference = VIN, refreshUser = vi.fn(async () => user()), onRender: () => void = () => undefined) {
  return <MemoryRouter initialEntries={[`/robots/${reference}?park=7&source=search`]}>
    <AuthContext.Provider value={{ user: currentUser, loading: false, login: async () => user(), refreshUser, logout: async () => undefined }}>
      <ParkScopeProvider><Profiler id="detail" onRender={onRender}><Routes>
        <Route path="/robots/:vin" element={<RobotPage apiClient={apiClient} />} />
      </Routes></Profiler><LocationProbe /></ParkScopeProvider>
    </AuthContext.Provider>
  </MemoryRouter>
}
beforeEach(() => {
  resourceStore.clearAll()
  localStorage.clear()
  sessionStorage.clear()
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  vi.spyOn(api, 'parks').mockResolvedValue([park])
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); resourceStore.clearAll() })

describe('robot detail presentation', () => {
  function view(overrides: Partial<React.ComponentProps<typeof RobotDetailView>> = {}) {
    return render(<MemoryRouter><RobotDetailView snapshot={snapshot()} relatedWork={[work]} relatedWorkError={null} workScopeLabel="Доступные роли очереди Tracker; выбор парка не определяет фактический парк робота" parkId={7} browserOnline canOpenCheck canOpenWork onRetrySnapshot={vi.fn()} onRetryWork={vi.fn()} {...overrides} /></MemoryRouter>)
  }
  it('separates robot identity, related scope, unavailable events and authorized destinations', () => {
    view()
    expect(screen.getByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
    expect(screen.getByText(VIN)).toBeInTheDocument()
    expect(screen.getByText(/выбор парка не определяет фактический парк/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
    expect(screen.getByRole('link', { name: 'Начать проверку робота' })).toHaveAttribute('href', `/robots/${VIN}/check?park=7`)
    expect(screen.getByText('История событий пока недоступна')).toBeInTheDocument()
  })
  it('labels generic model illustration independently of identity and falls back on load failure', () => {
    view()
    const illustration = screen.getByRole('img', { name: 'Иллюстрация модели робота' })
    expect(illustration).toHaveAttribute('decoding', 'async')
    expect(screen.getByText('Иллюстрация модели')).toBeInTheDocument()
    expect(screen.getByText(VIN)).toBeInTheDocument()
    fireEvent.error(illustration)
    expect(screen.queryByRole('img', { name: 'Иллюстрация модели робота' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Схема модели робота')).toBeInTheDocument()
  })
  it('renders readable work as text when destination capabilities are absent', () => {
    view({ canOpenCheck: false, canOpenWork: false })
    expect(screen.getByText('ROBOPARK-42')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
  it('does not mislabel a custom role without Tracker as a driver or an empty queue', () => {
    view({ relatedWork: null, canOpenWork: false })
    expect(screen.getByText(/Связанные задачи недоступны/)).toBeInTheDocument()
    expect(screen.queryByText(/Водитель|Связанных задач нет/)).not.toBeInTheDocument()
  })
})

describe('RobotPage ownership and lifecycle', () => {
  it.each(['447', 'yasadr00000000447'])('canonicalizes literal %s, preserves park and remembers the current user', async (reference) => {
    render(tree(client(), user(), reference))
    await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=7`))
    expect(loadRecentRobots(3)[0]).toMatchObject({ vin: VIN })
    expect(loadRecentRobots(4)).toEqual([])
  })
  it('rejects an invalid reference without network activity', async () => {
    const apiClient = client()
    render(tree(apiClient, user(), 'bad!'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/номер|VIN/i)
    expect(apiClient.emergencyResolve).not.toHaveBeenCalled()
  })
  it.each([409, 502])('keeps the snapshot and retries only related work after %s', async (status) => {
    const apiClient = client({ operatorRobotTickets: vi.fn().mockRejectedValueOnce(new ApiError(status, 'failed')).mockResolvedValue({ query: VIN, items: [work] }) })
    render(tree(apiClient))
    expect(await screen.findByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Повторить' }))
    expect(await screen.findByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeInTheDocument()
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
  })
  it('keeps an authorized snapshot but no tasks or retry on related-work 403', async () => {
    render(tree(client({ operatorRobotTickets: vi.fn().mockRejectedValue(new ApiError(403, 'denied')) })))
    expect(await screen.findByRole('alert')).toHaveTextContent('Нет доступа')
    expect(screen.getByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
    expect(screen.queryByText('ROBOPARK-42')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
  })
  it.each([403, 404])('suppresses the entire view on identity %s, with a search exit on 404', async (status) => {
    render(tree(client({ emergencySnapshot: vi.fn().mockRejectedValue(new ApiError(status, 'denied')) })))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Робот 447' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    if (status === 404) expect(screen.getByRole('link', { name: 'К поиску роботов' })).toHaveAttribute('href', '/robots')
  })
  it('opens a different permitted VIN after identity 403 without a stale denial frame', async () => {
    const apiClient = client({
      emergencyResolve: vi.fn(async (reference) => ({ vin: reference.endsWith('448') ? 'YASADR00000000448' : VIN, sections: [] })),
      emergencySnapshot: vi.fn(async (vin) => {
        if (vin === VIN) throw new ApiError(403, 'denied')
        return snapshot({ vin, short_number: '448' })
      }),
    })
    let capture = false
    const frames: string[] = []
    render(tree(apiClient, user(), VIN, undefined, () => { if (capture) frames.push(document.body.textContent ?? '') }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Нет доступа')
    capture = true
    fireEvent.click(screen.getByRole('button', { name: 'Другой робот' }))
    expect(await screen.findByRole('heading', { name: 'Робот 448' })).toBeInTheDocument()
    expect(apiClient.emergencySnapshot).toHaveBeenCalledWith('YASADR00000000448')
    expect(frames.length).toBeGreaterThan(0)
    expect(frames.every((frame) => !frame.includes('Нет доступа'))).toBe(true)
  })
  it('opens a new effective scope after identity 403 for the same principal without a stale denial frame', async () => {
    const apiClient = client({ emergencySnapshot: vi.fn().mockRejectedValueOnce(new ApiError(403, 'denied')).mockResolvedValue(snapshot()) })
    let capture = false
    const frames: string[] = []
    const onRender = () => { if (capture) frames.push(document.body.textContent ?? '') }
    const rendered = render(tree(apiClient, user(), VIN, undefined, onRender))
    expect(await screen.findByRole('alert')).toHaveTextContent('Нет доступа')
    capture = true
    rendered.rerender(tree(apiClient, user({ parks: [{ ...park, tracker_queue: 'NEW' }] }), VIN, undefined, onRender))
    expect(await screen.findByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(2)
    expect(frames.length).toBeGreaterThan(0)
    expect(frames.every((frame) => !frame.includes('Нет доступа'))).toBe(true)
  })
  it('keeps same-scope 403 fail-closed across same-ID auth publication and temporary park loading', async () => {
    const apiClient = client({ emergencySnapshot: vi.fn().mockRejectedValue(new ApiError(403, 'denied')) })
    const principal = user({ role: 'admin' })
    const rendered = render(tree(apiClient, principal))
    expect(await screen.findByRole('alert')).toHaveTextContent('Нет доступа')
    const parks = deferred<typeof principal.parks>()
    vi.mocked(api.parks).mockReturnValueOnce(parks.promise)
    rendered.rerender(tree(apiClient, { ...principal }))
    expect(screen.getByRole('alert')).toHaveTextContent('Нет доступа')
    await act(async () => parks.resolve([park]))
    expect(screen.getByRole('alert')).toHaveTextContent('Нет доступа')
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('heading', { name: 'Робот 447' })).not.toBeInTheDocument()
  })
  it.each([401, 403])('ignores an old scope late %s instead of blocking its newly permitted owner', async (status) => {
    const old = deferred<EmergencySnapshot>()
    const apiClient = client({ emergencySnapshot: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(snapshot()) })
    const refresh = vi.fn(async () => user())
    const rendered = render(tree(apiClient, user(), VIN, refresh))
    await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1))
    rendered.rerender(tree(apiClient, user({ parks: [{ ...park, tracker_queue: 'NEW' }] }), VIN, refresh))
    await screen.findByRole('heading', { name: 'Робот 447' })
    await act(async () => old.reject(new ApiError(status, 'old_scope_denied')))
    expect(screen.getByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(refresh).not.toHaveBeenCalled()
  })
  it('does not expose old tasks in any committed frame after tracker.read removal', async () => {
    const apiClient = client()
    let capture = false
    const frames: string[] = []
    const onRender = () => { if (capture) frames.push(document.body.textContent ?? '') }
    const rendered = render(tree(apiClient, user(), VIN, undefined, onRender))
    await screen.findByRole('link', { name: 'Открыть ROBOPARK-42' })
    capture = true
    rendered.rerender(tree(apiClient, user({ permissions: ['nav.emergency', 'nav.robot_search'] }), VIN, undefined, onRender))
    expect(frames.length).toBeGreaterThan(0)
    expect(frames.every((frame) => !frame.includes('ROBOPARK-42'))).toBe(true)
    expect(await screen.findByText(/Связанные задачи недоступны/)).toBeInTheDocument()
    expect(apiClient.operatorRobotTickets).toHaveBeenCalledTimes(1)
  })
  it('suppresses a prior principal before the first committed frame and late resolver cannot write recents or navigate', async () => {
    const old = deferred<{ vin: string; sections: [] }>()
    const apiClient = client({ emergencyResolve: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue({ vin: 'YASADR00000000448', sections: [] }), emergencySnapshot: vi.fn(async () => snapshot({ vin: 'YASADR00000000448', short_number: '448' })) })
    const rendered = render(tree(apiClient, user(), '447'))
    await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))
    rendered.rerender(tree(apiClient, user({ id: 4 }), '447'))
    await screen.findByRole('heading', { name: 'Робот 448' })
    await act(async () => old.resolve({ vin: VIN, sections: [] }))
    expect(loadRecentRobots(3)).toEqual([])
    expect(screen.getByLabelText('Адрес')).toHaveTextContent('/robots/YASADR00000000448')
    expect(apiClient.emergencySnapshot).not.toHaveBeenCalledWith(VIN)
  })
  it('drops a pending snapshot on unmount without cache publication or new recent writes', async () => {
    const pending = deferred<EmergencySnapshot>()
    const set = vi.spyOn(resourceStore, 'set')
    const apiClient = client({ emergencySnapshot: vi.fn(() => pending.promise) })
    const rendered = render(tree(apiClient))
    await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1))
    const rememberedAtResolution = loadRecentRobots(3)
    rendered.unmount()
    await act(async () => pending.resolve(snapshot()))
    expect(set).not.toHaveBeenCalled()
    expect(loadRecentRobots(3)).toEqual(rememberedAtResolution)
  })
  it('does not revive a released resolver after a StrictMode owner changes scope', async () => {
    const old = deferred<{ vin: string; sections: [] }>()
    const apiClient = client({ emergencyResolve: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue({ vin: VIN, sections: [] }) })
    const view = render(<StrictMode>{tree(apiClient)}</StrictMode>)
    await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))
    view.rerender(<StrictMode>{tree(apiClient, user({ permissions: [...user().permissions!, 'scope.new'] }))}</StrictMode>)
    await screen.findByRole('heading', { name: 'Робот 447' })
    const snapshotsBefore = vi.mocked(apiClient.emergencySnapshot).mock.calls.length
    await act(async () => old.resolve({ vin: 'YASADR00000000999', sections: [] }))
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(snapshotsBefore)
    expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?`)
    expect(loadRecentRobots(3).map((item) => item.vin)).toEqual([VIN])
  })
  it('starts a new VIN independently while an old snapshot is pending', async () => {
    const old = deferred<EmergencySnapshot>()
    const apiClient = client({ emergencyResolve: vi.fn(async (reference) => ({ vin: reference === '448' || reference.endsWith('448') ? 'YASADR00000000448' : VIN, sections: [] })), emergencySnapshot: vi.fn((vin) => vin === VIN ? old.promise : Promise.resolve(snapshot({ vin, short_number: '448' }))) })
    render(tree(apiClient))
    await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledWith(VIN))
    fireEvent.click(screen.getByRole('button', { name: 'Другой робот' }))
    await screen.findByRole('heading', { name: 'Робот 448' })
    await act(async () => old.resolve(snapshot()))
    expect(screen.queryByRole('heading', { name: 'Робот 447' })).not.toBeInTheDocument()
    // Resolving identity is enough to remember it in the unified check workflow;
    // the old snapshot must neither reorder recents nor overwrite the new robot.
    expect(loadRecentRobots(3).map((item) => item.vin)).toEqual(['YASADR00000000448', VIN])
  })
  it('drops late related work when effective queue scope changes and starts its new owner', async () => {
    const old = deferred<{ query: string; items: Blocker[] }>()
    const apiClient = client({ operatorRobotTickets: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue({ query: VIN, items: [{ ...work, key: 'NEW-1' }] }) })
    const set = vi.spyOn(resourceStore, 'set')
    const rendered = render(tree(apiClient))
    await waitFor(() => expect(apiClient.operatorRobotTickets).toHaveBeenCalledTimes(1))
    rendered.rerender(tree(apiClient, user({ parks: [{ ...park, tracker_queue: 'NEW' }] })))
    await screen.findByRole('link', { name: 'Открыть NEW-1' })
    set.mockClear()
    await act(async () => old.resolve({ query: VIN, items: [work] }))
    expect(set).not.toHaveBeenCalled()
    expect(screen.queryByText('ROBOPARK-42')).not.toBeInTheDocument()
  })
  it('does not show cached identity from a prior principal in any committed frame', async () => {
    const pending = deferred<EmergencySnapshot>()
    const apiClient = client()
    let capture = false
    const frames: string[] = []
    const onRender = () => { if (capture) frames.push(document.body.textContent ?? '') }
    const rendered = render(tree(apiClient, user(), VIN, undefined, onRender))
    await screen.findByRole('heading', { name: 'Робот 447' })
    vi.mocked(apiClient.emergencySnapshot).mockReturnValue(pending.promise)
    capture = true
    rendered.rerender(tree(apiClient, user({ id: 4 }), VIN, undefined, onRender))
    expect(frames.length).toBeGreaterThan(0)
    expect(frames.every((frame) => !frame.includes('Робот 447') && !frame.includes('ROBOPARK-42'))).toBe(true)
  })
  it('removes retained identity after a refresh denial, including while the device reports offline', async () => {
    const apiClient = client()
    render(tree(apiClient))
    await screen.findByRole('link', { name: 'Открыть ROBOPARK-42' })
    const denied = deferred<EmergencySnapshot>()
    vi.mocked(apiClient.emergencySnapshot).mockReturnValueOnce(denied.promise)
    fireEvent.click(screen.getByRole('button', { name: /^Обновить данные/ }))
    await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(2))
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    await act(async () => { window.dispatchEvent(new Event('offline')); denied.reject(new ApiError(403, 'denied')) })
    expect(screen.getByRole('alert')).toHaveTextContent('Нет доступа')
    expect(screen.queryByRole('heading', { name: 'Робот 447' })).not.toBeInTheDocument()
    expect(screen.queryByText('ROBOPARK-42')).not.toBeInTheDocument()
  })
  it('keeps once-only 401 refresh above a same-ID park-provider loading remount', async () => {
    const apiClient = client({ emergencySnapshot: vi.fn().mockRejectedValue(new ApiError(401, 'expired')) })
    const refresh = vi.fn()
    function Session() {
      const [current, setCurrent] = useState(user({ role: 'admin' }))
      refresh.mockImplementation(async () => { const next = { ...current }; setCurrent(next); return next })
      return <MemoryRouter initialEntries={[`/robots/${VIN}?park=7`]}><AuthContext.Provider value={{ user: current, loading: false, login: async () => current, logout: async () => undefined, refreshUser: refresh }}><ParkScopeProvider><Routes><Route path="/robots/:vin" element={<RobotPage apiClient={apiClient} />} /></Routes></ParkScopeProvider></AuthContext.Provider></MemoryRouter>
    }
    render(<Session />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Сессия истекла')
    await waitFor(() => expect(api.parks).toHaveBeenCalledTimes(2))
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
    expect(screen.queryByText(VIN)).not.toBeInTheDocument()
  })
  it('ages freshness while background polling is suspended and retains the snapshot offline', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T09:05:00Z'))
    const apiClient = client()
    render(tree(apiClient))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByText(/Данные актуальны/)).toBeInTheDocument()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { vi.advanceTimersByTime(330_000) })
    expect(screen.getByText(/Данные устарели/)).toBeInTheDocument()
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    act(() => { window.dispatchEvent(new Event('offline')) })
    expect(screen.getByText('Нет сети на этом устройстве')).toBeInTheDocument()
    expect(screen.getByText(VIN)).toBeInTheDocument()
    expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
    expect(apiClient.operatorRobotTickets).toHaveBeenCalledTimes(1)
    expect(Object.keys(localStorage).filter((key) => key.startsWith('robopark:res:'))).toEqual([])
  })
})
