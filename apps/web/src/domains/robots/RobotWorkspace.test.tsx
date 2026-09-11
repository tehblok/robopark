import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError, type DiagnosticEvent, type EmergencySnapshot, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { resourceStore } from '../../lib/resource'
import { RobotPage } from './RobotPage'
import { RobotCheckPage } from './RobotCheckPage'
import { RobotsPage } from './RobotsPage'

const VIN = 'YASADR00000000447'
const park = { id: 8, name: 'Юг', tag: 'Beta', tracker_queue: 'ROBOPARK' }
const user: User = { id: 3, username: 'operator', role: 'operator', access_status: 'approved',
  permissions: ['nav.robot_search', 'nav.tasks', 'nav.emergency', 'tracker.read'], parks: [park] }
const snapshot: EmergencySnapshot = { vin: VIN, short_number: '447', observed_at: new Date().toISOString(),
  online: true, speed: 1, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
  mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true, connection: 'lte',
  error_banner: null, lat: null, lon: null, heading_deg: null, wheels_fault: ['fl'] }
const leadingDiagnostic: DiagnosticEvent = { id: 'lidar', rule_id: 1, source_path: 'errors.0', source_segments: ['errors', 0],
  raw_value: 'LIDAR_OFFLINE', title: 'Передний лидар недоступен', description: 'Проверьте питание и соединение лидара.',
  severity: 'critical', sort_order: 0, part: 'Передний лидар', view: 'front', x: .25, y: .6, indicator: 'point' }
function client() {
  return { emergencyResolve: vi.fn(async () => ({ vin: VIN, sections: [{ id: 'wheels', title: 'Колёса' }] })),
    emergencySnapshot: vi.fn(async () => snapshot),
    emergencySection: vi.fn(async () => ({ id: 'wheels', title: 'Колёса', fields: [{ label: 'Колёса', lines: ['Проверены'] }] })),
    mechanicRobotTickets: vi.fn(async () => ({ query: VIN, items: [] })),
    operatorRobotTickets: vi.fn(async () => ({ query: VIN, items: [] })),
    trackerRobotTickets: vi.fn(async () => ({ query: VIN, items: [] })) }
}
function Probe() { const location = useLocation(); return <output aria-label="Адрес">{location.pathname}{location.search}</output> }
function tree(apiClient: ReturnType<typeof client>, entry = `/robots/${VIN}?park=8`, principal = user) {
  return <MemoryRouter initialEntries={[entry]}><AuthContext.Provider value={{ user: principal, loading: false,
    refreshUser: async () => principal, login: async () => principal, logout: async () => undefined }}>
    <ParkScopeContext.Provider value={{ parkId: 8, selectedPark: park, parks: [park], loading: false,
      locked: false, setParkId: vi.fn(), refreshParks: async () => undefined }}>
      <Routes><Route path="/robots/:vin" element={<RobotPage apiClient={apiClient} checkClient={apiClient} />} />
        <Route path="/robots/:vin/check" element={<RobotCheckPage resolverClient={apiClient} checkClient={apiClient} />} /></Routes><Probe />
    </ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}
function selectSecondaryTab(name: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Ещё' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}
beforeEach(() => { resourceStore.clearAll(); vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  vi.spyOn(api, 'operatorRobotTickets').mockResolvedValue({ query: VIN, items: [] }) })
afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('exposes one identity, related tasks and diagnostics and keeps tabs in the robot workspace', async () => {
  const apiClient = client(); render(tree(apiClient))
  await screen.findByRole('heading', { name: 'Робот 447' })
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getAllByRole('heading', { name: 'Робот 447' })).toHaveLength(1)
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Состояние', 'Ошибки', 'Схема', 'Карта'])
  expect(screen.getByRole('button', { name: 'Ещё' })).toBeVisible()
  selectSecondaryTab('Задачи')
  expect(screen.getByRole('tab', { name: 'Задачи' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Задачи')
  expect(await screen.findByRole('heading', { name: 'Связанные задачи' })).toBeVisible()
  selectSecondaryTab('Телеметрия')
  expect(screen.getByRole('tab', { name: 'Телеметрия' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Телеметрия')
  expect(within(screen.getByRole('tabpanel')).getByText('Заряд').parentElement).toHaveTextContent('80 %')
  expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8&tab=telemetry`)
  expect(screen.queryByRole('link', { name: /Начать проверку|Карточка робота/ })).not.toBeInTheDocument()
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
})

it('uses the compact diagnostic summary in the production robot route', async () => {
  const apiClient = client()
  apiClient.emergencySnapshot.mockResolvedValue({ ...snapshot, lat: 55.75, lon: 37.62, diagnostic_events: [leadingDiagnostic] })
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=state`))

  const summary = await screen.findByRole('region', { name: 'Состояние робота' })
  expect(summary).toHaveTextContent('Робот 447')
  expect(summary).toHaveTextContent('Робот на связи')
  expect(summary).toHaveTextContent('Заряд 80 %')
  expect(summary).toHaveTextContent('Данные актуальны')
  expect(summary).toHaveTextContent(leadingDiagnostic.description)
  const supplementary = within(summary).getByText('VIN и координаты').closest('details')!
  expect(supplementary).not.toHaveAttribute('open')
  expect(supplementary).toHaveTextContent(VIN)
  expect(supplementary).toHaveTextContent('55.75, 37.62')
  expect(screen.queryByText('Иллюстрация модели')).not.toBeInTheDocument()

  fireEvent.click(within(summary).getByRole('button', { name: 'Показать неисправность' }))
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8&tab=scheme`))
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Схема')
  expect(screen.getByRole('button', { name: 'Спереди', pressed: true })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Выбранная ошибка' })).toHaveTextContent(leadingDiagnostic.description)
})

it('states the clean diagnostic verdict in the compact summary', async () => {
  const apiClient = client()
  apiClient.emergencySnapshot.mockResolvedValue({
    ...snapshot,
    diagnostic_events: [],
    error_banner: null,
    wheels_fault: [],
  })
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=state`))

  expect(await screen.findByRole('region', { name: 'Состояние робота' }))
    .toHaveTextContent('Активных ошибок нет')
})

it.each(['pending', 'failed'] as const)('loads direct related tasks while the Emergency snapshot is %s and keeps tabs usable', async state => {
  const apiClient = client()
  let rejectSnapshot!: (error: unknown) => void
  apiClient.emergencySnapshot.mockImplementation(() => new Promise((_resolve, reject) => { rejectSnapshot = reject }))
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=tasks`))
  await waitFor(() => expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1))
  if (state === 'failed') await act(async () => rejectSnapshot(new ApiError(502, 'upstream_failed')))
  expect(await screen.findByText('Связанных задач нет в доступной области.')).toBeVisible()
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Задачи')
  expect(apiClient.operatorRobotTickets).toHaveBeenCalledWith(VIN)
  selectSecondaryTab('Телеметрия')
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Телеметрия')
  selectSecondaryTab('Задачи')
  expect(screen.getByRole('tab', { name: 'Задачи' })).toHaveAttribute('aria-selected', 'true')
  expect(await screen.findByText('Связанных задач нет в доступной области.')).toBeVisible()
})

it('can enter related tasks after a cold snapshot error on another tab', async () => {
  const apiClient = client()
  apiClient.emergencySnapshot.mockRejectedValue(new ApiError(502, 'upstream_failed'))
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=telemetry`))
  await screen.findByRole('alert')
  selectSecondaryTab('Задачи')
  expect(screen.getByRole('tab', { name: 'Задачи' })).toHaveAttribute('aria-selected', 'true')
  expect(await screen.findByText('Связанных задач нет в доступной области.')).toBeVisible()
})

it.each([401, 403])('does not load tasks before resolution and keeps resolver %s authoritative', async status => {
  const apiClient = client()
  let rejectResolve!: (error: unknown) => void
  apiClient.emergencyResolve.mockImplementation(() => new Promise((_resolve, reject) => { rejectResolve = reject }))
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=tasks`))
  await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))
  expect(apiClient.operatorRobotTickets).not.toHaveBeenCalled()
  await act(async () => rejectResolve(new ApiError(status, 'forbidden')))
  await screen.findByRole('alert')
  expect(apiClient.operatorRobotTickets).not.toHaveBeenCalled()
  expect(apiClient.emergencySnapshot).not.toHaveBeenCalled()
})

it.each([401, 403])('removes related tasks if the active snapshot returns %s after tasks loaded', async status => {
  const apiClient = client()
  let rejectSnapshot!: (error: unknown) => void
  apiClient.emergencySnapshot.mockImplementation(() => new Promise((_resolve, reject) => { rejectSnapshot = reject }))
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=tasks`))
  await screen.findByText('Связанных задач нет в доступной области.')
  await act(async () => rejectSnapshot(new ApiError(status, 'forbidden')))
  await screen.findByRole('alert')
  expect(screen.queryByRole('heading', { name: 'Связанные задачи' })).not.toBeInTheDocument()
})

it('keeps related work disabled without tracker.read while snapshot is pending', async () => {
  const apiClient = client()
  apiClient.emergencySnapshot.mockImplementation(() => new Promise(() => {}))
  render(tree(apiClient, `/robots/${VIN}?park=8&tab=tasks`, { ...user, permissions: ['nav.robot_search', 'nav.emergency'] }))
  expect(await screen.findByText(/Связанные задачи недоступны: нет разрешения/)).toBeVisible()
  expect(apiClient.operatorRobotTickets).not.toHaveBeenCalled()
})

it('canonicalizes a short reference without loading the snapshot or resolver twice', async () => {
  const apiClient = client(); render(tree(apiClient, '/robots/447?park=8&tab=scheme'))
  await screen.findByRole('heading', { name: 'Робот 447' })
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8&tab=scheme`))
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Схема')
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
  expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
})

it('legacy check URL exposes the same identity and related tasks with its selected photo tab', async () => {
  const apiClient = client(); render(tree(apiClient, `/robots/${VIN}/check?park=8&tab=scheme`))
  await screen.findByRole('heading', { name: 'Робот 447' })
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Схема')
  const photo = within(screen.getByRole('tabpanel')).getByRole('img')
  expect(photo).toHaveAttribute('src', expect.stringContaining('top.png'))
  expect(photo).toHaveAttribute('loading', 'lazy')
  expect(screen.getByLabelText('Адрес')).toHaveTextContent('park=8&tab=scheme')
  await waitFor(() => expect(screen.getByLabelText('Адрес')).not.toHaveTextContent('/check'))
  expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
})

it('redirects a legacy check URL even when the resolver cannot load diagnostics', async () => {
  const apiClient = client()
  apiClient.emergencyResolve.mockRejectedValue(new ApiError(503, 'tracker_token_not_configured'))
  render(tree(apiClient, `/robots/${VIN}/check?park=8&tab=errors`))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8&tab=errors`)
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
})

it('opens robots by number within the selected park without requesting the registry', async () => {
  const rows = [{ vin: VIN, short_number: '447', park_ids: [8], state: 'unknown' as const, telemetry: null,
    task_count: 2, task_keys: ['ROBOPARK-1', 'ROBOPARK-2'], issue_keys: ['ROBOPARK-1', 'ROBOPARK-2'], error_count: null }]
  const apiClient = { ...client(), robotRegistry: vi.fn(async () => ({ items: rows, total: 1, offset: 0, limit: 50, has_more: false, partial: true, source_complete: true, source: 'scoped_tracker_issues' as const, park_id: 8 })) }
  render(<MemoryRouter initialEntries={['/robots?park=8']}><AuthContext.Provider value={{ user, loading: false,
    refreshUser: async () => user, login: async () => user, logout: async () => undefined }}>
    <ParkScopeContext.Provider value={{ parkId: 8, selectedPark: park, parks: [park], loading: false,
      locked: false, setParkId: vi.fn(), refreshParks: async () => undefined }}>
      <RobotsPage apiClient={apiClient} /><Probe />
    </ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  await act(async () => undefined)
  expect(apiClient.robotRegistry).not.toHaveBeenCalled()
  expect(screen.queryByRole('heading', { name: 'Роботы в работе' })).not.toBeInTheDocument()
  expect(screen.getAllByRole('heading', { level: 2 }).map(heading => heading.textContent)).toEqual([
    'Открыть по номеру или сканировать', 'Недавние роботы',
  ])
  expect(apiClient.emergencyResolve).not.toHaveBeenCalled()
  expect(apiClient.emergencySnapshot).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Номер или VIN робота'), { target: { value: '447' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти робота' }))
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8`))
  expect(apiClient.emergencyResolve).toHaveBeenCalledWith('447')
  expect(apiClient.robotRegistry).not.toHaveBeenCalled()
})

it.each([VIN, '447'])('uses validated reference %s for related work without making protected check requests', async reference => {
  const apiClient = client()
  render(tree(apiClient, `/robots/${reference}?park=8&tab=scheme`, { ...user, permissions: ['nav.robot_search', 'tracker.read'] }))
  expect(await screen.findByText(/Диагностика недоступна/)).toBeVisible()
  await act(async () => undefined)
  expect(apiClient.emergencyResolve).not.toHaveBeenCalled()
  expect(apiClient.emergencySnapshot).not.toHaveBeenCalled()
  expect(apiClient.emergencySection).not.toHaveBeenCalled()
  expect(apiClient.operatorRobotTickets).toHaveBeenCalledWith(reference)
  expect(screen.queryByText(/Робот на связи|Данные актуальны/)).not.toBeInTheDocument()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
})
