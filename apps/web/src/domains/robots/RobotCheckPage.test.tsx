import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { RobotCheckPage } from './RobotCheckPage'
import { loadRecentRobots } from './recentRobots'
import type { RobotResolverApiClient } from './RobotResolver'
const VIN = 'YASADR00000000447'
const user: User = { id: 3, username: 'op', role: 'operator', access_status: 'approved', permissions: ['nav.emergency'], parks: [] }
const sections = [{ id: 'wheels', title: 'Колёса' }]
const checkClient = { emergencySnapshot: vi.fn(() => new Promise<never>(() => undefined)), emergencySection: vi.fn(() => new Promise<never>(() => undefined)) }
function Probe() { const location = useLocation(); const navigate = useNavigate(); return <><output aria-label="Адрес">{location.pathname}{location.search}</output><button onClick={() => navigate('/robots/448/check?park=7')}>Другой робот</button></> }
function tree(resolverClient: RobotResolverApiClient, options: { currentUser?: User; loading?: boolean; refreshUser?: () => Promise<User>; reference?: string; search?: string } = {}) {
  return <MemoryRouter initialEntries={[`/robots/${options.reference ?? VIN}/check${options.search ?? '?park=7&tab=wheels'}`]}>
    <AuthContext.Provider value={{ user: options.currentUser ?? user, loading: false, refreshUser: options.refreshUser ?? (async () => user), login: async () => user, logout: async () => undefined }}>
      <ParkScopeContext.Provider value={{ parkId: 7, selectedPark: null, parks: [], loading: options.loading ?? false, locked: false, setParkId: vi.fn(), refreshParks: async () => undefined }}>
        <Routes><Route path="/robots/:vin/check" element={<RobotCheckPage resolverClient={resolverClient} checkClient={checkClient} />} /><Route path="/robots/:vin" element={<RobotCheckPage resolverClient={resolverClient} checkClient={checkClient} />} /></Routes><Probe />
      </ParkScopeContext.Provider>
    </AuthContext.Provider>
  </MemoryRouter>
}
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true) })
afterEach(() => vi.restoreAllMocks())
it.each(['447', VIN.toLowerCase()])('canonicalizes literal %s, keeps numeric park and valid tab, remembers current success', async reference => {
  const resolver = { emergencyResolve: vi.fn(async () => ({ vin: VIN, sections })) }
  render(tree(resolver, { reference })); await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=7&tab=wheels`))
  expect(await screen.findByRole('heading', { name: 'Проверка робота' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Карточка робота' })).not.toBeInTheDocument()
  expect(loadRecentRobots(user.id)[0].vin).toBe(VIN)
})
it('removes invalid tab and nonnumeric park', async () => {
  render(tree({ emergencyResolve: vi.fn(async () => ({ vin: VIN, sections })) }, { search: '?park=oops&tab=secret' }))
  await waitFor(() => expect(screen.getByLabelText('Адрес').textContent).toBe(`/robots/${VIN}`))
})
it.each([false, true])('classifies configuration 403 before scope and permits settings only by capability %s', async allowed => {
  render(tree({ emergencyResolve: vi.fn().mockRejectedValue(new ApiError(403, 'emergency_cookie_invalid', 'config-id')) }, { currentUser: { ...user, role: allowed ? 'custom' : 'operator', permissions: allowed ? ['nav.emergency', 'nav.admin.emergency'] : ['nav.emergency'] } }))
  await screen.findByRole('heading', { name: 'Интеграция проверки робота требует внимания' })
  expect(screen.getByText(/config-id/)).toBeInTheDocument()
  expect(Boolean(screen.queryByRole('link', { name: 'Открыть настройки' }))).toBe(allowed)
  expect(screen.getByText(allowed
    ? 'Cookie проверки робота отклонена. Скопируйте свежую сессию и повторите.'
    : 'Обратитесь к администратору для проверки подключения.')).toBeVisible()
  expect(document.body).not.toHaveTextContent('emergency_cookie_invalid')
})
it('keeps 401 durable through same-principal auth publication, loading and reference changes; refreshes once', async () => {
  const refreshUser = vi.fn(async () => ({ ...user }))
  const resolver = { emergencyResolve: vi.fn().mockRejectedValue(new ApiError(401, 'emergency_cookie_invalid')) }
  const view = render(tree(resolver, { refreshUser })); await screen.findByRole('heading', { name: 'Сессия истекла' })
  view.rerender(tree(resolver, { refreshUser, currentUser: { ...user }, loading: true }))
  view.rerender(tree(resolver, { refreshUser, currentUser: { ...user }, loading: false }))
  fireEvent.click(screen.getByRole('button', { name: 'Другой робот' })); await act(async () => undefined)
  expect(screen.getByRole('heading', { name: 'Сессия истекла' })).toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1); expect(resolver.emergencyResolve).toHaveBeenCalledTimes(1)
})
it('403 survives same-scope loading but a different VIN can load without denial', async () => {
  const resolver = { emergencyResolve: vi.fn().mockRejectedValueOnce(new ApiError(403, 'forbidden')).mockResolvedValue({ vin: 'YASADR00000000448', sections: [] }) }
  const view = render(tree(resolver)); await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree(resolver, { loading: true })); view.rerender(tree(resolver))
  expect(resolver.emergencyResolve).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Другой робот' }))
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  await waitFor(() => expect(resolver.emergencyResolve).toHaveBeenCalledWith('448'))
})
it('changing effective permissions releases resource-specific forbidden state', async () => {
  const resolver = { emergencyResolve: vi.fn().mockRejectedValueOnce(new ApiError(403, 'forbidden')).mockResolvedValue({ vin: VIN, sections }) }
  const view = render(tree(resolver)); await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree(resolver, { currentUser: { ...user, permissions: ['nav.emergency', 'scope.new'] } }))
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  await waitFor(() => expect(resolver.emergencyResolve).toHaveBeenCalledTimes(2))
})
it('old resolver completion cannot navigate or remember after principal changes or StrictMode cleanup', async () => {
  let resolve!: (v: { vin: string; sections: typeof sections }) => void
  const resolver = { emergencyResolve: vi.fn().mockImplementationOnce(() => new Promise(res => { resolve = res })).mockImplementation(() => new Promise(() => undefined)) }
  const view = render(<StrictMode>{tree(resolver)}</StrictMode>); await act(async () => undefined)
  view.rerender(<StrictMode>{tree(resolver, { currentUser: { ...user, id: 4 } })}</StrictMode>); await act(async () => undefined)
  await act(async () => resolve({ vin: 'YASADR00000000999', sections }))
  expect(loadRecentRobots(3)).toEqual([]); expect(loadRecentRobots(4)).toEqual([])
  expect(screen.getByLabelText('Адрес')).not.toHaveTextContent('999')
})
it('404 offers search while retryable failure exposes request id and retry', async () => {
  const resolver = { emergencyResolve: vi.fn().mockRejectedValueOnce(new ApiError(503, 'failed', 'request-id')).mockRejectedValue(new ApiError(404, 'not_found')) }
  render(tree(resolver)); await screen.findByText(/request-id/)
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await screen.findByRole('heading', { name: 'Не найдено' })
  expect(screen.getByRole('link', { name: 'К поиску роботов' })).toHaveAttribute('href', '/robots')
})
it('manual resolve offline is one-shot, not permission to auto-resolve on a later offline event', async () => {
  const connection = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  const resolver = { emergencyResolve: vi.fn().mockRejectedValue(new ApiError(503, 'failed')) }
  render(tree(resolver)); await act(async () => undefined)
  expect(resolver.emergencyResolve).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить проверку' }))
  await waitFor(() => expect(resolver.emergencyResolve).toHaveBeenCalledTimes(1))
  connection.mockReturnValue(true); fireEvent(window, new Event('online')); await act(async () => undefined)
  expect(resolver.emergencyResolve).toHaveBeenCalledTimes(2)
  connection.mockReturnValue(false); fireEvent(window, new Event('offline')); await act(async () => undefined)
  expect(resolver.emergencyResolve).toHaveBeenCalledTimes(2)
})
