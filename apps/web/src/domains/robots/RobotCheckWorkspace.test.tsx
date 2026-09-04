import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, type EmergencySnapshot, type EmergencySectionDetail, type User } from '../../api'
import { RobotCheckWorkspace, type RobotCheckApiClient } from './RobotCheckWorkspace'

const VIN = 'YASADR00000000447'
const user: User = { id: 3, username: 'op', role: 'operator', access_status: 'approved', permissions: ['nav.emergency'], parks: [] }
const sections = [{ id: 'wheels', title: 'Колёса' }, { id: 'power', title: 'Питание' }]
function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return { vin: VIN, short_number: '447', observed_at: '2026-09-02T09:05:00Z', online: true, speed: 0, charge_percent: 80, battery1_percent: 75, battery2_percent: 85, disk_percent: 20, mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true, connection: 'lte', error_banner: null, lat: null, lon: null, heading_deg: null, wheels_fault: [], ...overrides }
}
function client(overrides: Partial<RobotCheckApiClient> = {}): RobotCheckApiClient {
  return { emergencySnapshot: vi.fn(async () => snapshot()), emergencySection: vi.fn(async (_vin, id) => ({ id, title: id, fields: [{ label: 'Состояние колёс', lines: ['Секция получена'] }] })), ...overrides }
}
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (error: unknown) => void
  return { promise: new Promise<T>((res, rej) => { resolve = res; reject = rej }), resolve: (v: T) => resolve(v), reject: (e: unknown) => reject(e) }
}
function tree(apiClient: RobotCheckApiClient, activeTab = 'wheels', overrides: Partial<React.ComponentProps<typeof RobotCheckWorkspace>> = {}) {
  return <MemoryRouter><RobotCheckWorkspace vin={VIN} user={user} sections={sections} activeTab={activeTab} onTabChange={vi.fn()} apiClient={apiClient} {...overrides} /></MemoryRouter>
}
beforeEach(() => { vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-09-02T09:05:00Z')); vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true); Object.defineProperty(document, 'hidden', { configurable: true, value: false }) })
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })
it('shows decision data before the active dynamic section and manually refreshes', async () => {
  const apiClient = client(); render(tree(apiClient))
  expect(await screen.findByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
  expect(screen.getByText('Робот на связи')).toBeInTheDocument()
  expect(screen.getByText(/Данные на 2 сентября 2026/)).toBeInTheDocument()
  expect(apiClient.emergencySection).toHaveBeenCalledWith(VIN, 'wheels')
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Колёса')
  expect(screen.getByText('Секция получена').tagName).toBe('PRE')
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(2))
})
it('does not automatically load cold offline but permits a manual check', async () => {
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  const apiClient = client(); render(tree(apiClient, 'map')); await act(async () => undefined)
  expect(apiClient.emergencySnapshot).not.toHaveBeenCalled()
  expect(screen.getByText('Нет сети на этом устройстве')).toBeInTheDocument()
  expect(screen.queryByText('Робот не в сети')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить проверку' }))
  expect(await screen.findByRole('heading', { name: 'Робот 447' })).toBeInTheDocument()
})
it('retains the last snapshot offline and after partial failures, with local retry', async () => {
  const apiClient = client(); render(tree(apiClient)); await screen.findByText('Секция получена')
  expect(screen.getByText(/Данные актуальны/)).toBeInTheDocument()
  vi.mocked(apiClient.emergencySnapshot).mockRejectedValue(new ApiError(503, 'failure', 'snap-id'))
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  await screen.findByText(/snap-id/)
  expect(screen.getByText(/Данные устарели/)).toBeInTheDocument()
  expect(screen.getByText('Секция получена')).toBeInTheDocument()
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false); fireEvent(window, new Event('offline'))
  expect(screen.getByText(VIN)).toBeInTheDocument()
  expect(screen.getByText('Нет сети на этом устройстве')).toBeInTheDocument()
})
it('keeps successful snapshot while a section fails and exposes section retry', async () => {
  render(tree(client({ emergencySection: vi.fn().mockRejectedValue(new ApiError(503, 'failed', 'section-id')) })))
  await screen.findByRole('heading', { name: 'Робот 447' })
  const panel = screen.getByRole('tabpanel')
  expect(within(panel).getByText(/section-id/)).toBeInTheDocument()
  expect(within(panel).getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
})
it.each([401, 403])('clears protected data immediately on %s even when sibling hangs', async status => {
  const section = deferred<EmergencySectionDetail>()
  const apiClient = client(); const onAuthorizationFailure = vi.fn()
  render(tree(apiClient, 'wheels', { onAuthorizationFailure })); await screen.findByText('Секция получена')
  vi.mocked(apiClient.emergencySnapshot).mockRejectedValue(new ApiError(status, status === 401 ? 'emergency_cookie_invalid' : 'forbidden'))
  vi.mocked(apiClient.emergencySection).mockReturnValue(section.promise)
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  await screen.findByRole('heading', { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' })
  expect(screen.queryByText(VIN)).not.toBeInTheDocument()
  expect(onAuthorizationFailure).toHaveBeenCalledTimes(1)
  await act(async () => section.resolve({ id: 'wheels', title: 'Колёса', fields: [{ label: 'Late', lines: ['SECRET'] }] }))
  expect(screen.queryByText('SECRET')).not.toBeInTheDocument()
  fireEvent(document, new Event('visibilitychange')); await act(async () => undefined)
  expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(2)
})
it('configuration errors preserve request id and restrict settings by capability', async () => {
  const apiClient = client({ emergencySnapshot: vi.fn().mockRejectedValue(new ApiError(403, 'emergency_cookie_invalid', 'config-id')) })
  const view = render(tree(apiClient, 'map'))
  await screen.findByRole('heading', { name: 'Интеграция проверки робота требует внимания' })
  expect(screen.getByText(/config-id/)).toBeInTheDocument()
  expect(screen.getByText(/Обратитесь к администратору/)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Открыть настройки' })).not.toBeInTheDocument()
  view.rerender(tree(apiClient, 'map', { user: { ...user, role: 'custom', permissions: ['nav.admin.emergency'] } }))
  expect(await screen.findByRole('link', { name: 'Открыть настройки' })).toHaveAttribute('href', '/admin/emergency/config')
  expect(document.body).not.toHaveTextContent('emergency_cookie_invalid')
})
it('a section denial wins immediately over a hung snapshot and ignores its later success', async () => {
  const oldSnapshot = deferred<EmergencySnapshot>()
  render(tree(client({ emergencySnapshot: vi.fn(() => oldSnapshot.promise), emergencySection: vi.fn().mockRejectedValue(new ApiError(401, 'unauthorized')) })))
  await screen.findByRole('heading', { name: 'Сессия истекла' })
  await act(async () => oldSnapshot.resolve(snapshot()))
  expect(screen.queryByText(VIN)).not.toBeInTheDocument()
})
it('a tab switch starts its own request and rejects the inactive tab snapshot and section', async () => {
  const oldSnapshot = deferred<EmergencySnapshot>(); const oldSection = deferred<EmergencySectionDetail>()
  const apiClient = client({ emergencySnapshot: vi.fn().mockReturnValueOnce(oldSnapshot.promise).mockResolvedValue(snapshot({ short_number: 'CURRENT' })), emergencySection: vi.fn().mockReturnValueOnce(oldSection.promise).mockResolvedValue({ id: 'power', title: 'Питание', fields: [{ label: 'Питание', lines: ['CURRENT SECTION'] }] }) })
  const view = render(tree(apiClient)); await act(async () => undefined)
  view.rerender(tree(apiClient, 'power'))
  await screen.findByText('CURRENT SECTION')
  await act(async () => { oldSnapshot.resolve(snapshot({ short_number: 'OLD' })); oldSection.resolve({ id: 'wheels', title: 'Old', fields: [{ label: 'old', lines: ['OLD SECTION'] }] }) })
  expect(screen.getByRole('heading', { name: 'Робот CURRENT' })).toBeInTheDocument()
  expect(screen.queryByText('OLD SECTION')).not.toBeInTheDocument()
})
it('does not publish old VIN or inactive tab completions, including StrictMode replay', async () => {
  const old = deferred<EmergencySnapshot>(); const oldSection = deferred<EmergencySectionDetail>()
  const apiClient = client({ emergencySnapshot: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(snapshot()), emergencySection: vi.fn().mockReturnValueOnce(oldSection.promise).mockResolvedValue({ id: 'power', title: 'Питание', fields: [] }) })
  const view = render(<StrictMode>{tree(apiClient)}</StrictMode>); await act(async () => undefined)
  view.rerender(<StrictMode>{tree(apiClient, 'power', { vin: 'YASADR00000000448' })}</StrictMode>); await act(async () => undefined)
  await act(async () => { old.resolve(snapshot({ short_number: 'OLD' })); oldSection.resolve({ id: 'wheels', title: 'old', fields: [{ label: 'Old', lines: ['SECRET'] }] }) })
  expect(screen.queryByText('Робот OLD')).not.toBeInTheDocument()
  expect(screen.queryByText('SECRET')).not.toBeInTheDocument()
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Питание')
})
it('renders missing telemetry as unknown and never fetches a static section', async () => {
  const apiClient = client({ emergencySnapshot: vi.fn(async () => snapshot({ speed: null, charge_percent: null, connection: null, lte_label: null })) })
  render(tree(apiClient, 'telemetry')); await screen.findByRole('heading', { name: 'Робот 447' })
  const panel = screen.getByRole('tabpanel')
  for (const label of ['Скорость', 'Заряд', 'Батарея 1', 'Батарея 2', 'Диск', 'Режим', 'ICP', 'LTE', 'Соединение']) expect(within(panel).getByText(label, { selector: 'dt' })).toBeInTheDocument()
  expect(within(panel).getAllByText('Нет данных').length).toBeGreaterThanOrEqual(4)
  expect(apiClient.emergencySection).not.toHaveBeenCalled()
})
it('uses selected photo, precise known-wheel controls and truthful unknown faults, with fallback', async () => {
  const onTabChange = vi.fn()
  render(tree(client({ emergencySnapshot: vi.fn(async () => snapshot({ wheels_fault: ['fl', 'body', 'sensor-unknown'] })) }), 'scheme', { onTabChange }))
  await screen.findByRole('heading', { name: 'Робот 447' })
  expect(screen.getByRole('button', { name: 'Сверху', pressed: true })).toBeInTheDocument()
  expect(screen.getAllByRole('img')).toHaveLength(1)
  expect(screen.getByRole('img')).toHaveAttribute('src', expect.stringContaining('top.png'))
  const wheel = screen.getByRole('button', { name: 'Переднее левое колесо: неисправность' })
  wheel.focus(); expect(wheel).toHaveFocus(); fireEvent.click(wheel)
  expect(screen.getByText('Выбрано: Переднее левое колесо')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть данные колёс' })); expect(onTabChange).toHaveBeenCalledWith('wheels')
  expect(screen.getByText('Неисправность колёс: точное расположение не определено')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /корпус/i })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Спереди' }))
  expect(screen.getByRole('img')).toHaveAttribute('src', expect.stringContaining('front.png'))
  expect(screen.queryByRole('button', { name: /Среднее левое колесо:/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Изометрия' }))
  fireEvent.click(screen.getByRole('button', { name: 'Показать колёса сверху' }))
  fireEvent.error(screen.getByRole('img'))
  expect(screen.getByRole('img', { name: 'Схема модели робота' })).toBeInTheDocument()
  expect(screen.getByText('Иллюстрация модели')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Переднее левое колесо: неисправность' })).toBeInTheDocument()
})
