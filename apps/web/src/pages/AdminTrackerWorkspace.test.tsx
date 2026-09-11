import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api'
import { resourceStore } from '../lib/resource'
import { installMatchMedia, renderApp, testUser } from '../test/renderApp'

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('shows issue filters before the explicitly disclosed policy editor at 390px', async () => {
  installMatchMedia({ width: 390 })
  vi.spyOn(api, 'trackerPolicy').mockResolvedValue({ operator_show_untagged: false, operator_show_raw: false, operator_show_firmware_profile: false, mechanic_can_write: false })
  vi.spyOn(api, 'trackerIssues').mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0, has_more: false })
  renderApp('/admin/tracker?park=7', testUser({ role: 'admin', permissions: ['nav.admin.tracker', 'tracker.read'] }))
  expect(await screen.findByRole('button', { name: 'Фильтры' })).toBeVisible()
  expect(screen.queryByRole('checkbox', { name: 'Запись механика' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Настроить политику Tracker' }))
  expect(screen.getByRole('checkbox', { name: 'Запись механика' })).toBeVisible()
})
