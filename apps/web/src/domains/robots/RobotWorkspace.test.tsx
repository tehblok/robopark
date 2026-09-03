import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type EmergencySnapshot, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { resourceStore } from '../../lib/resource'
import { RobotPage } from './RobotPage'
import { RobotCheckPage } from './RobotCheckPage'

const VIN = 'YASADR00000000447'
const park = { id: 8, name: 'Юг', tag: 'Beta', tracker_queue: 'ROBOPARK' }
const user: User = { id: 3, username: 'operator', role: 'operator', access_status: 'approved',
  permissions: ['nav.robot_search', 'nav.tasks', 'nav.emergency', 'tracker.read'], parks: [park] }
const snapshot: EmergencySnapshot = { vin: VIN, short_number: '447', observed_at: new Date().toISOString(),
  online: true, speed: 1, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
  mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true, connection: 'lte',
  error_banner: null, lat: null, lon: null, heading_deg: null, wheels_fault: ['fl'] }
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
beforeEach(() => { resourceStore.clearAll(); vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  vi.spyOn(api, 'operatorRobotTickets').mockResolvedValue({ query: VIN, items: [] }) })
afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('exposes one identity, related tasks and diagnostics and keeps tabs in the robot workspace', async () => {
  const apiClient = client(); render(tree(apiClient))
  await screen.findByRole('heading', { name: 'Робот 447' })
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  expect(screen.getAllByRole('heading', { name: 'Робот 447' })).toHaveLength(1)
  expect(screen.getByRole('heading', { name: 'Связанные задачи' })).toBeVisible()
  fireEvent.click(screen.getByRole('tab', { name: 'Телеметрия' }))
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Телеметрия')
  expect(within(screen.getByRole('tabpanel')).getByText('Заряд').parentElement).toHaveTextContent('80 %')
  expect(screen.getByLabelText('Адрес')).toHaveTextContent(`/robots/${VIN}?park=8&tab=telemetry`)
  expect(screen.queryByRole('link', { name: /Начать проверку|Карточка робота/ })).not.toBeInTheDocument()
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
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
  expect(screen.getByRole('heading', { name: 'Связанные задачи' })).toBeVisible()
  expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Схема')
  expect(within(screen.getByRole('tabpanel')).getByRole('img')).toHaveAttribute('src', expect.stringContaining('top.png'))
  expect(screen.getByLabelText('Адрес')).toHaveTextContent('park=8&tab=scheme')
  expect(apiClient.emergencySnapshot).toHaveBeenCalledTimes(1)
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
