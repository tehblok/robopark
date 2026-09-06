import { resourceStore } from '../../lib/resource'
import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { api, type AdminRole } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

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

it.each(['royal', 'lead'])('previews %s effective permissions according to owner-only RBAC rules', async slug => {
  installMatchMedia()
  const actor = userEvent.setup()
  const role: AdminRole = { id: 3, slug, name: slug === 'royal' ? 'Владелец' : 'Старший', description: '', is_system: slug === 'royal', is_active: true, permissions: ['nav.tasks', ...(slug === 'royal' ? [] : ['users.approve'])], user_count: 1 }
  vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([
    { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
    { key: 'reports.create', label: 'Создавать репорты', category: 'action', sort_order: 2 },
    { key: 'users.approve', label: 'Одобрять регистрации', category: 'action', sort_order: 3 },
  ])
  const update = vi.spyOn(api, 'updateAdminRole').mockResolvedValue(role)
  renderApp('/admin/roles', testUser({ role: 'royal', permissions: ['roles.manage'] }))
  await actor.click(await screen.findByRole('button', { name: `Открыть роль ${role.name}` }))
  const detail = screen.getByRole('region', { name: 'Детали' })
  const preview = within(detail).getByRole('region', { name: 'Итоговые доступы' })
  expect(preview).toHaveTextContent('Работа')
  if (slug === 'royal') {
    expect(preview).toHaveTextContent('Создавать репорты')
    expect(preview).toHaveTextContent('Одобрять регистрации')
    expect(within(detail).getByText(/Владелец всегда имеет все доступы/)).toBeVisible()
    for (const checkbox of within(detail).getAllByRole('checkbox')) {
      expect(checkbox).toBeChecked()
      expect(checkbox).toBeDisabled()
    }
  } else {
    expect(preview).not.toHaveTextContent('Одобрять регистрации')
    expect(within(detail).queryByRole('checkbox', { name: 'Одобрять регистрации' })).not.toBeInTheDocument()
    await actor.click(within(detail).getByRole('checkbox', { name: 'Создавать репорты' }))
    expect(preview).toHaveTextContent('Создавать репорты')
  }
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  expect(update).toHaveBeenCalledWith(3, slug === 'royal'
    ? { name: 'Владелец', description: '' }
    : { name: 'Старший', description: '', permissions: ['nav.tasks', 'reports.create'] })
})


it('refreshes role rows on focus while keeping the open role draft', async () => {
  installMatchMedia()
  const role: AdminRole = { id: 2, slug: 'lead', name: 'Старший смены', description: '', is_system: false, is_active: true, permissions: [], user_count: 2 }
  const read = vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
  renderApp('/admin/roles', testUser({ role: 'royal', permissions: ['roles.manage'] }))
  fireEvent.click(await screen.findByRole('button', { name: /Открыть роль Старший смены/ }))
  const name = screen.getByLabelText('Название')
  fireEvent.change(name, { target: { value: 'Несохранённое название' } })
  read.mockResolvedValue([{ ...role, name: 'Новое серверное имя' }])
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByRole('button', { name: 'Открыть роль Новое серверное имя' })
  expect(name).toHaveValue('Несохранённое название')
  expect(read).toHaveBeenCalledTimes(2)
})
