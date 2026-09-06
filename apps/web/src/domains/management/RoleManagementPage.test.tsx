import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api, type AdminRole } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

afterEach(() => vi.restoreAllMocks())

it('edits role grants in a detail pane and separates deletion from routine save', async () => {
  installMatchMedia()
  const actor = userEvent.setup()
  const role: AdminRole = { id: 2, slug: 'lead', name: 'Старший смены', description: '', is_system: false, is_active: true, permissions: ['nav.tasks'], user_count: 2 }
  vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([
    { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
    { key: 'reports.create', label: 'Создавать репорты', category: 'action', sort_order: 2 },
  ])
  const update = vi.spyOn(api, 'updateAdminRole').mockResolvedValue({ ...role, permissions: ['reports.create'] })
  renderApp('/admin/roles', testUser({ role: 'royal', permissions: ['roles.manage'] }))
  await actor.click(await screen.findByRole('button', { name: /Старший смены/ }))
  const detail = screen.getByRole('region', { name: 'Детали' })
  await actor.click(within(detail).getByRole('checkbox', { name: 'Работа' }))
  await actor.click(within(detail).getByRole('checkbox', { name: 'Создавать репорты' }))
  const preview = within(detail).getByRole('region', { name: 'Итоговые доступы' })
  expect(preview).not.toHaveTextContent('Работа')
  expect(preview).toHaveTextContent('Создавать репорты')
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  expect(update).toHaveBeenCalledWith(2, { name: 'Старший смены', description: '', permissions: ['reports.create'] })
  const danger = within(detail).getByRole('region', { name: 'Опасные действия' })
  expect(within(danger).getByRole('button', { name: 'Удалить роль' })).toBeEnabled()
  expect(within(danger).queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
})
