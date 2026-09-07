import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError, type EmergencySnapshot, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { EmergencyViewer } from './EmergencyViewer'

const user: User = { id: 3, username: 'op', role: 'admin', access_status: 'approved', permissions: ['nav.emergency'], parks: [] }
const snapshot: EmergencySnapshot = { vin: 'VIN447', short_number: '447', observed_at: '2026-09-02T09:05:00Z', online: true, speed: 0, charge_percent: 80, battery1_percent: 75, battery2_percent: 85, disk_percent: 20, mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true, connection: 'lte', error_banner: null, lat: null, lon: null, heading_deg: null, wheels_fault: [] }
function tree() {
  return <MemoryRouter initialEntries={['/?q=447&tab=wheels']}><AuthContext.Provider value={{ user, loading: false, login: async () => user, logout: async () => undefined, refreshUser: async () => user }}><EmergencyViewer /></AuthContext.Provider></MemoryRouter>
}
beforeEach(() => {
  vi.useFakeTimers(); resourceStore.clearAll(); localStorage.clear()
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  vi.spyOn(api, 'emergencyResolve').mockResolvedValue({ vin: 'VIN447', sections: [{ id: 'wheels', title: 'Колёса' }] })
  vi.spyOn(api, 'emergencySnapshot').mockResolvedValue(snapshot)
  vi.spyOn(api, 'emergencySection').mockResolvedValue({ id: 'wheels', title: 'Колёса', fields: [{ label: 'Проверка', lines: ['Получено'] }] })
})
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); resourceStore.clearAll() })
const flush = () => act(async () => undefined)
it('automatically updates snapshot and selected section without polling offline or hidden', async () => {
  render(tree()); await flush()
  expect(screen.getByText('Получено')).toBeInTheDocument()
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('tab', { name: 'Карта' })); await flush()
  fireEvent.click(screen.getByRole('tab', { name: 'Колёса' })); await flush()
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(1)
  expect(api.emergencySection).toHaveBeenCalledTimes(1)
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => vi.advanceTimersByTimeAsync(10_000))
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(1)
  hidden.mockReturnValue(false); fireEvent(document, new Event('visibilitychange')); await flush()
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(2)
  const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  fireEvent(window, new Event('offline'))
  await act(async () => vi.advanceTimersByTimeAsync(10_000))
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(2)
  online.mockReturnValue(true); fireEvent(window, new Event('online')); await flush()
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(3)
  expect(api.emergencySection).toHaveBeenCalledTimes(3)
})
it('retains the cookie repair action after automatic polling detects invalid credentials', async () => {
  render(tree()); await flush()
  vi.mocked(api.emergencySnapshot).mockRejectedValue(new ApiError(403, 'emergency_cookie_invalid'))
  await act(async () => vi.advanceTimersByTimeAsync(10_000))
  expect(screen.getByRole('link')).toHaveAttribute('href', '/admin')
  const calls = vi.mocked(api.emergencySnapshot).mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(30_000))
  expect(api.emergencySnapshot).toHaveBeenCalledTimes(calls)
})

// Existing lifecycle assertions use the minimum jitter; capacity tests cover dispersion.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
