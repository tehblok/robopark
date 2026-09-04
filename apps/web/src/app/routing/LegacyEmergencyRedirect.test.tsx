import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { testUser } from '../../test/renderApp'
import { LegacyEmergencyRedirect, type LegacyRobotCheckApiClient } from './LegacyEmergencyRedirect'

const user = testUser({ role: 'driver', permissions: ['nav.robot_search', 'nav.emergency'] })
const resolved = { vin: 'yasadr00000000447', sections: [{ id: 'wheels', title: 'Колёса' }] }
function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output data-testid="location">{location.pathname}{location.search}</output>
    <button onClick={() => navigate('/emergency?q=448&park=8&tab=map')}>Другой запрос</button></>
}
function renderLegacy(path: string, client: LegacyRobotCheckApiClient) {
  const tree = (currentUser: User | null) => <StrictMode><MemoryRouter initialEntries={[path]}>
    <AuthContext.Provider value={{ user: currentUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>
      <Routes>
        <Route path="/emergency" element={<LegacyEmergencyRedirect apiClient={client} />} />
        <Route path="/robots" element={<h1>Роботы</h1>} />
        <Route path="/robots/:vin/check" element={<h1>Проверка робота</h1>} />
      </Routes><LocationProbe />
    </AuthContext.Provider>
  </MemoryRouter></StrictMode>
  const view = render(tree(user))
  return { ...view, rerenderUser: (next: User | null) => view.rerender(tree(next)) }
}
async function expectLocation(path: string) {
  await waitFor(() => expect(screen.getByTestId('location').textContent).toBe(path))
}

describe('LegacyEmergencyRedirect', () => {
  it.each([
    ['/emergency?q=%20447%20&tab=%20wheels%20&park=7', '/robots/YASADR00000000447/check?park=7&tab=wheels'],
    ['/emergency?robot=447', '/robots/YASADR00000000447/check'],
    ['/emergency?q=447&robot=wrong&park=oops&tab=map', '/robots/YASADR00000000447/check?tab=map'],
  ])('resolves %s into %s', async (path, expected) => {
    const emergencyResolve = vi.fn(async () => resolved)
    renderLegacy(path, { emergencyResolve })
    await expectLocation(expected)
    expect(emergencyResolve).toHaveBeenCalledWith('447')
  })

  it.each([
    ['/emergency?park=7', '/robots?park=7'],
    ['/emergency?q=%20&robot=447&park=-7&tab=wheels', '/robots'],
  ])('sends empty input to search without a resolve call: %s', async (path, expected) => {
    const emergencyResolve = vi.fn()
    renderLegacy(path, { emergencyResolve })
    await expectLocation(expected)
    expect(emergencyResolve).not.toHaveBeenCalled()
  })

  it('encodes the resolved VIN as one uppercase path segment', async () => {
    renderLegacy('/emergency?q=447', { emergencyResolve: vi.fn(async () => ({ vin: 'vin/a b', sections: [] })) })
    await expectLocation('/robots/VIN%2FA%20B/check')
  })

  it.each([
    [401, 'emergency_cookie_invalid', 'Сессия истекла', false],
    [403, 'emergency_cookie_invalid', 'Требуется настройка', false],
    [403, 'forbidden', 'Нет доступа', false],
    [404, 'missing', 'Не найдено', false],
    [503, 'failed', 'Сервис временно недоступен', true],
  ] as const)('classifies %s/%s with request id and retry policy', async (status, detail, title, retryable) => {
    renderLegacy('/emergency?q=447&park=7', { emergencyResolve: vi.fn().mockRejectedValue(new ApiError(status, detail, 'legacy-request-id')) })
    expect(await screen.findByRole('heading', { name: title })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent('legacy-request-id')
    expect(Boolean(screen.queryByRole('button', { name: 'Повторить' }))).toBe(retryable)
    expect(screen.getByRole('link', { name: 'К поиску роботов' })).toHaveAttribute('href', '/robots?park=7')
    expect(screen.getByRole('alert')).not.toHaveTextContent(/\bEmergency\b/)
  })

  it('retries a transient failure and navigates on success', async () => {
    const emergencyResolve = vi.fn().mockRejectedValueOnce(new ApiError(503)).mockResolvedValue(resolved)
    renderLegacy('/emergency?q=447', { emergencyResolve })
    fireEvent.click(await screen.findByRole('button', { name: 'Повторить' }))
    await expectLocation('/robots/YASADR00000000447/check')
    expect(emergencyResolve).toHaveBeenCalledTimes(2)
  })

  it.each(['principal', 'permissions', 'logout', 'input', 'unmount'] as const)('ignores late resolver completion after %s changes', async change => {
    let finish!: (value: typeof resolved) => void
    const emergencyResolve = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve })).mockImplementation(() => new Promise(() => undefined))
    const view = renderLegacy('/emergency?q=447', { emergencyResolve })
    expect(await screen.findByRole('status', { name: 'Находим робота' })).toBeVisible()
    await waitFor(() => expect(emergencyResolve).toHaveBeenCalledTimes(1))
    if (change === 'principal') view.rerenderUser({ ...user, id: 99 })
    if (change === 'permissions') view.rerenderUser({ ...user, permissions: [] })
    if (change === 'logout') view.rerenderUser(null)
    if (change === 'input') fireEvent.click(screen.getByRole('button', { name: 'Другой запрос' }))
    if (change === 'unmount') view.unmount()
    await act(async () => finish(resolved))
    if (change !== 'unmount') expect(screen.getByTestId('location')).toHaveTextContent('/emergency?')
    expect(screen.queryByRole('heading', { name: 'Проверка робота' })).not.toBeInTheDocument()
  })
})
