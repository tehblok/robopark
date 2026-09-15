import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
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

it('shows Errors and Readings tabs only to an approved admin', async () => {
  vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([])
  const admin = renderApp('/admin/emergency/config', testUser({ role: 'admin', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('tab', { name: 'Ошибки' })).toBeVisible()
  expect(screen.getByRole('tab', { name: 'Показания' })).toBeVisible()
  admin.unmount()

  renderApp('/admin/emergency/config', testUser({ role: 'royal', permissions: ['nav.admin.emergency'] }))
  expect(await screen.findByRole('searchbox', { name: 'Поиск разделов' })).toBeVisible()
  expect(screen.queryByRole('tab', { name: 'Ошибки' })).not.toBeInTheDocument()
  expect(screen.queryByRole('tab', { name: 'Показания' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Новое правило' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Новый раздел' })).not.toBeInTheDocument()
})
