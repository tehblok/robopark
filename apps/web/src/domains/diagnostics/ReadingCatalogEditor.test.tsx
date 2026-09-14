import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  api,
  ApiError,
  type EmergencyReading,
  type EmergencyReadingDraft,
  type User,
} from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { AdminEmergencyConfig } from '../../pages/AdminEmergencyConfig'

const admin: User = { id: 1, username: 'admin', role: 'admin', access_status: 'approved', parks: [], permissions: ['nav.admin.emergency'] }
const baseReading: EmergencyReading = {
  id: 1,
  section_id: 'sensors',
  path: 'battery.voltage',
  label: 'Напряжение',
  display_kind: 'number',
  unit: 'В',
  precision: 1,
  enabled_path: null,
  no_data_values: [],
  warning_below: null,
  warning_above: null,
  critical_below: null,
  critical_above: null,
  view: 'front',
  x: 0.25,
  y: 0.4,
  label_direction: 'auto',
  is_enabled: true,
  sort_order: 0,
}
const secondReading: EmergencyReading = { ...baseReading, id: 2, path: 'velocity', label: 'Скорость', sort_order: 1 }

function tree() {
  return <MemoryRouter initialEntries={['/admin/emergency/config?tab=readings']}>
    <AuthContext.Provider value={{ user: admin, loading: false, login: async () => admin, refreshUser: async () => admin, logout: async () => undefined }}>
      <AdminEmergencyConfig />
    </AuthContext.Provider>
  </MemoryRouter>
}

beforeEach(() => {
  resourceStore.clearAll()
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([
    { id: 'sensors', title: 'Датчики', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] },
    { id: 'safety', title: 'Безопасность', sort_order: 1, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] },
  ])
  vi.spyOn(api, 'emergencyReadings').mockResolvedValue({ readings: [baseReading, secondReading], etag: '"readings-1"' })
  vi.spyOn(api, 'discoverEmergencyReadings').mockResolvedValue([
    { path: 'parktronics.lt', value_type: 'number', example: '320' },
    { path: 'parktronics.ltEnabled', value_type: 'boolean', example: 'true' },
    { path: 'velocity', value_type: 'number', example: '0' },
  ])
  vi.spyOn(api, 'createEmergencyReading').mockImplementation(async draft => ({ ...draft, id: 3 }))
  vi.spyOn(api, 'updateEmergencyReading').mockImplementation(async (id, changes) => ({ ...baseReading, ...changes, id }))
  vi.spyOn(api, 'disableEmergencyReading').mockResolvedValue({ ...baseReading, is_enabled: false })
  vi.spyOn(api, 'reorderEmergencyReadings').mockResolvedValue({ readings: [secondReading, baseReading], etag: '"readings-2"' })
  vi.spyOn(api, 'deleteEmergencyReading').mockResolvedValue(undefined)
  vi.stubGlobal('PointerEvent', MouseEvent)
})

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); resourceStore.clearAll() })

it('preserves reading ETags and sends discovery and mutation contracts without server-owned fields', async () => {
  vi.restoreAllMocks()
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response('[]', { headers: { ETag: '"catalog-1"' } }))
    .mockResolvedValueOnce(new Response('[{"path":"velocity","value_type":"number","example":"0"}]'))
    .mockResolvedValueOnce(new Response(JSON.stringify(baseReading), { status: 201 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(baseReading)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...baseReading, is_enabled: false })))
    .mockResolvedValueOnce(new Response('[]', { headers: { ETag: '"catalog-2"' } }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetcher)
  const catalog = await api.emergencyReadings()
  await api.discoverEmergencyReadings(' R 10/7 ')
  await api.createEmergencyReading(baseReading)
  await api.updateEmergencyReading(1, { ...baseReading, label: 'Ток', id: 99, sort_order: 7 } as unknown as EmergencyReadingDraft)
  await api.disableEmergencyReading(1)
  const reordered = await api.reorderEmergencyReadings([1], catalog.etag!)
  await api.deleteEmergencyReading(1)

  expect(catalog.etag).toBe('"catalog-1"')
  expect(reordered.etag).toBe('"catalog-2"')
  expect(fetcher.mock.calls[1][0]).toBe('/api/admin/emergency-readings/discovered?vin=+R+10%2F7+')
  expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual(expect.objectContaining({ label: 'Ток' }))
  expect(JSON.parse(fetcher.mock.calls[3][1].body)).not.toEqual(expect.objectContaining({ id: expect.anything(), sort_order: expect.anything() }))
  expect(new Headers(fetcher.mock.calls[5][1].headers).get('If-Match')).toBe('"catalog-1"')
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    '/api/admin/emergency-readings',
    '/api/admin/emergency-readings/discovered?vin=+R+10%2F7+',
    '/api/admin/emergency-readings',
    '/api/admin/emergency-readings/1',
    '/api/admin/emergency-readings/1',
    '/api/admin/emergency-readings/reorder',
    '/api/admin/emergency-readings/1',
  ])
})

it('discovers a scalar, suggests its companion availability and sentinel, places it and saves one previewed reading', async () => {
  render(tree())
  fireEvent.change(await screen.findByLabelText('Номер робота для примера'), { target: { value: 'R-107' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти показания' }))
  await waitFor(() => expect(api.discoverEmergencyReadings).toHaveBeenCalledWith('R-107', expect.any(AbortSignal)))
  fireEvent.change(screen.getByRole('searchbox', { name: 'Поиск показаний' }), { target: { value: 'parktronics.lt' } })
  fireEvent.click(await screen.findByRole('button', { name: 'Выбрать parktronics.lt' }))

  expect(screen.getByLabelText('JSON-путь')).toHaveValue('parktronics.lt')
  expect(screen.getByLabelText('JSON-путь')).toHaveAttribute('readonly')
  expect(screen.getByLabelText('Путь доступности')).toHaveValue('parktronics.ltEnabled')
  expect(screen.getByLabelText('Нет показания')).toHaveValue('2147483647')
  fireEvent.change(screen.getByLabelText('Название показания'), { target: { value: 'Левый парктроник' } })
  fireEvent.change(screen.getByLabelText('Формат'), { target: { value: 'distance' } })
  fireEvent.change(screen.getByLabelText('Диагностический блок'), { target: { value: 'safety' } })
  fireEvent.change(screen.getByLabelText('Единица'), { target: { value: 'мм' } })
  fireEvent.change(screen.getByLabelText('Направление подписи'), { target: { value: 'right' } })
  const image = screen.getByRole('img', { name: 'Вид спереди' })
  vi.spyOn(image, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 20, width: 200, height: 400 } as DOMRect)
  fireEvent.pointerUp(image, { clientX: 180, clientY: 220 })

  expect(screen.getByRole('region', { name: 'Предпросмотр показания' })).toHaveTextContent('Левый парктроник')
  expect(screen.getByRole('region', { name: 'Предпросмотр показания' })).toHaveTextContent('320 мм')
  expect(screen.queryByText(/порог/i)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить показание' }))
  await waitFor(() => expect(api.createEmergencyReading).toHaveBeenCalledWith(expect.objectContaining({
    path: 'parktronics.lt', enabled_path: 'parktronics.ltEnabled', no_data_values: [2147483647],
    display_kind: 'distance', section_id: 'safety', label_direction: 'right', x: 0.4, y: 0.5,
  })))
  expect(await screen.findByText('Показание сохранено.')).toBeVisible()
})

it('edits one item and supports reorder, disable, delete and access-denied recovery', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть показание Напряжение' }))
  expect(screen.getByLabelText('Название показания')).toHaveValue('Напряжение')
  fireEvent.change(screen.getByLabelText('Название показания'), { target: { value: 'Напряжение АКБ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить показание' }))
  await waitFor(() => expect(api.updateEmergencyReading).toHaveBeenCalledWith(1, expect.objectContaining({ label: 'Напряжение АКБ' })))
  await screen.findByText('Показание сохранено.')
  fireEvent.click(screen.getByRole('button', { name: 'Ниже: Напряжение АКБ' }))
  await waitFor(() => expect(api.reorderEmergencyReadings).toHaveBeenCalledWith(expect.any(Array), '"readings-1"'))
  fireEvent.click(screen.getByRole('button', { name: 'Отключить показание' }))
  await waitFor(() => expect(api.disableEmergencyReading).toHaveBeenCalledWith(1))
  fireEvent.click(screen.getByRole('button', { name: 'Удалить показание' }))
  await waitFor(() => expect(api.deleteEmergencyReading).toHaveBeenCalledWith(1))

  vi.mocked(api.emergencyReadings).mockRejectedValueOnce(new ApiError(403))
  fireEvent.click(screen.getByRole('button', { name: 'Обновить каталог' }))
  expect(await screen.findByText('Нет доступа к настройке показаний.')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Новое показание' })).not.toBeInTheDocument()
})

it('reloads the authoritative catalog after an ETag conflict and explains the retry', async () => {
  vi.mocked(api.reorderEmergencyReadings).mockRejectedValueOnce(new ApiError(409, 'emergency_readings_catalog_changed'))
  render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Ниже: Напряжение' }))

  await waitFor(() => expect(api.emergencyReadings).toHaveBeenCalledTimes(2))
  expect(screen.getByText('Каталог изменился. Обновите список и повторите действие.')).toBeVisible()
})
