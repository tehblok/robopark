import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api'
import { resourceStore } from '../lib/resource'
import { installMatchMedia, renderApp, testUser } from '../test/renderApp'

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('shows section search and identities before one explicitly selected editor at 390px', async () => {
  installMatchMedia({ width: 390 })
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([{ id: 'state', title: 'Состояние', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] }])
  renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('searchbox', { name: 'Поиск разделов' })).toBeVisible()
  expect(screen.queryByLabelText('Название state')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('ID раздела')).not.toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть раздел Состояние' }))
  expect(screen.getByLabelText('Название state')).toBeVisible()
})

it('lets approved royal open every robot-check setting including error mapping', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([])
  const admin = renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('tab', { name: 'Ошибки' })).toBeVisible()
  expect(screen.getByRole('tab', { name: 'Показания' })).toBeVisible()
  admin.unmount()

  renderApp('/admin/emergency/config', testUser({ role: 'royal', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('searchbox', { name: 'Поиск разделов' })).toBeVisible()
  expect(screen.getByRole('tab', { name: 'Ошибки' })).toBeVisible()
  expect(screen.getByRole('tab', { name: 'Показания' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Новый раздел' })).toBeVisible()
})

it('hides cached diagnostic sections after a denied revalidation', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([{ id: 'state', title: 'Состояние', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] }])
  const first = renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))
  await screen.findByRole('button', { name: 'Открыть раздел Состояние' })
  first.unmount()
  vi.mocked(api.adminEmergencySections).mockRejectedValue(new ApiError(403, 'forbidden'))
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)

  renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Каталог недоступен')
  expect(screen.queryByRole('button', { name: 'Открыть раздел Состояние' })).not.toBeInTheDocument()
  expect(screen.queryByText('Разделы проверки робота ещё не настроены')).not.toBeInTheDocument()
})

it('does not show the previous administrator’s sections while another account loads', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([{ id: 'state', title: 'Состояние', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] }])
  const first = renderApp('/admin/emergency/config', testUser({ id: 1, role: 'admin', permissions: ['nav.admin.emergency'] }))
  await screen.findByRole('button', { name: 'Открыть раздел Состояние' })
  first.unmount()
  vi.mocked(api.adminEmergencySections).mockReturnValue(new Promise(() => {}))

  renderApp('/admin/emergency/config', testUser({ id: 2, username: 'second-admin', role: 'admin', permissions: ['nav.admin.emergency'] }))
  expect(screen.queryByRole('button', { name: 'Открыть раздел Состояние' })).not.toBeInTheDocument()
})

it('shows a server error instead of an empty catalog on a cold failure', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockRejectedValue(new ApiError(503, 'upstream_unavailable'))
  renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить разделы')
  expect(screen.queryByText('Разделы проверки робота ещё не настроены')).not.toBeInTheDocument()
})

it('checks for updated diagnostic sections when the catalog is opened again', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValueOnce([{ id: 'state', title: 'Состояние', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] }])
    .mockResolvedValueOnce([{ id: 'wheels', title: 'Колёса', sort_order: 0, is_enabled: true, formatter: null, meta: null, roles: ['admin'], fields: [] }])
  const actor = testUser({ role: 'admin', permissions: ['nav.admin.emergency'] })
  const first = renderApp('/admin/emergency/config', actor)
  await screen.findByRole('button', { name: 'Открыть раздел Состояние' })
  first.unmount()

  renderApp('/admin/emergency/config', actor)
  expect(await screen.findByRole('button', { name: 'Открыть раздел Колёса' })).toBeVisible()
  expect(api.adminEmergencySections).toHaveBeenCalledTimes(2)
})
