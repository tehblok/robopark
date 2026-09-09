import { act, fireEvent, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type AdminRole, type AdminUser } from '../../api'
import { resourceStore } from '../../lib/resource'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }
const south = { ...north, id: 8, name: 'Южный', tag: 'south' }
const roles: AdminRole[] = [
  { id: 1, slug: 'mechanic', name: 'Механик', description: '', is_system: true, is_active: true, user_count: 1, permissions: ['nav.tasks', 'nav.reports', 'reports.create'] },
  { id: 2, slug: 'driver', name: 'Водитель', description: '', is_system: true, is_active: true, user_count: 0, permissions: ['nav.tasks'] },
]
const account: AdminUser = {
  id: 2, username: 'mechanic-two', role: 'mechanic', role_id: 1, access_status: 'approved', is_active: true,
  must_change_password: false, parks: [north], permissions: roles[0].permissions, role_permissions: roles[0].permissions,
}

beforeEach(() => {
  installMatchMedia()
  vi.spyOn(api, 'parks').mockResolvedValue([north, south])
  vi.spyOn(api, 'adminUsers').mockResolvedValue([account])
  vi.spyOn(api, 'adminRoles').mockResolvedValue(roles)
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([
    { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
    { key: 'nav.reports', label: 'Репорты', category: 'nav', sort_order: 2 },
    { key: 'reports.create', label: 'Создавать репорты', category: 'action', sort_order: 3 },
  ])
})
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks() })

function openAccounts() {
  return renderApp('/admin/users?park=7', testUser({ role: 'royal', permissions: ['users.manage'], parks: [north, south] }))
}

it('previews effective permissions after role changes and individual grants or revokes', async () => {
  const actor = userEvent.setup()
  openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  const preview = within(detail).getByRole('region', { name: 'Итоговые доступы' })
  expect(preview).toHaveTextContent('Работа')
  expect(preview).toHaveTextContent('Создавать репорты')
  await actor.selectOptions(within(detail).getByRole('combobox', { name: 'Роль' }), 'driver')
  expect(preview).not.toHaveTextContent('Создавать репорты')
  await actor.click(within(detail).getByRole('checkbox', { name: 'Репорты' }))
  expect(preview).toHaveTextContent('Репорты')
  await actor.click(within(detail).getByRole('checkbox', { name: /Работа/ }))
  expect(preview).not.toHaveTextContent('Работа')
})

it('removes owner-only approval from non-royal role previews and the submitted account permissions', async () => {
  const actor = userEvent.setup()
  vi.mocked(api.adminRoles).mockResolvedValue([...roles, { ...roles[0], id: 3, slug: 'custom_approver', name: 'Согласующий', is_system: false, permissions: ['nav.tasks', 'users.approve'] }])
  vi.mocked(api.adminRolePermissionCatalog).mockResolvedValue([
    { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
    { key: 'users.approve', label: 'Одобрять регистрации', category: 'action', sort_order: 2 },
  ])
  const update = vi.spyOn(api, 'updateAdminUser').mockResolvedValue({ ...account, role: 'custom_approver', permissions: ['nav.tasks'] })
  openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  await actor.selectOptions(within(detail).getByRole('combobox', { name: 'Роль' }), 'custom_approver')
  const preview = within(detail).getByRole('region', { name: 'Итоговые доступы' })
  expect(preview).toHaveTextContent('Работа')
  expect(preview).not.toHaveTextContent('Одобрять регистрации')
  expect(within(detail).queryByRole('checkbox', { name: 'Одобрять регистрации' })).not.toBeInTheDocument()
  expect(within(detail).getByText(/Одобрение регистраций.*только.*владельц/)).toBeVisible()
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(update).toHaveBeenCalledWith(2, expect.objectContaining({ role_slug: 'custom_approver', permissions: ['nav.tasks'] })))
})

it('royal role previews all permissions immutably and restores non-royal semantics after demotion', async () => {
  const actor = userEvent.setup()
  vi.mocked(api.adminRoles).mockResolvedValue([...roles, { ...roles[0], id: 3, slug: 'royal', name: 'Владелец', permissions: [] }])
  vi.mocked(api.adminRolePermissionCatalog).mockResolvedValue([
    { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
    { key: 'users.approve', label: 'Одобрять регистрации', category: 'action', sort_order: 2 },
  ])
  const update = vi.spyOn(api, 'updateAdminUser').mockResolvedValue({ ...account, role: 'royal', permissions: ['nav.tasks', 'users.approve'] })
  openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  await actor.selectOptions(within(detail).getByRole('combobox', { name: 'Роль' }), 'royal')
  const preview = within(detail).getByRole('region', { name: 'Итоговые доступы' })
  expect(preview).toHaveTextContent('Работа')
  expect(preview).toHaveTextContent('Одобрять регистрации')
  expect(within(detail).queryByRole('checkbox', { name: /Работа|Одобрять регистрации/ })).not.toBeInTheDocument()
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(update).toHaveBeenCalledWith(2, expect.objectContaining({ role_slug: 'royal', permissions: ['nav.tasks', 'users.approve'] })))
  await screen.findByText('Изменения сохранены')
  await actor.selectOptions(within(detail).getByRole('combobox', { name: 'Роль' }), 'driver')
  expect(preview).not.toHaveTextContent('Одобрять регистрации')
})

it('saves owner password, role, park and section changes and isolates account deletion', async () => {
  const actor = userEvent.setup()
  const update = vi.spyOn(api, 'updateAdminUser').mockResolvedValue({ ...account, role: 'driver', parks: [south], permissions: ['nav.tasks', 'nav.reports'], must_change_password: true })
  const remove = vi.spyOn(api, 'deleteAdminUser').mockResolvedValue(undefined)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  await actor.selectOptions(within(detail).getByRole('combobox', { name: 'Роль' }), 'driver')
  await actor.type(within(detail).getByLabelText('Новый пароль'), 'NewPassword!2026')
  await actor.click(within(detail).getByRole('checkbox', { name: 'Требовать смену пароля при входе' }))
  await actor.click(within(detail).getByRole('button', { name: /Парки.*Выбрано: 1/ }))
  await actor.click(within(detail).getByRole('checkbox', { name: 'Северный' }))
  await actor.click(within(detail).getByRole('checkbox', { name: 'Южный' }))
  await actor.click(within(detail).getByRole('checkbox', { name: 'Репорты' }))
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(update).toHaveBeenCalledWith(2, {
    role_slug: 'driver', is_active: true, must_change_password: true, tracker_login: null,
    park_ids: [8], permissions: ['nav.tasks', 'nav.reports'], access_status: 'approved', password: 'NewPassword!2026',
  }))
  expect(await screen.findByText('Изменения сохранены')).toBeVisible()
  expect(within(detail).getByLabelText('Новый пароль')).toHaveValue('')
  const danger = within(detail).getByRole('region', { name: 'Опасные действия' })
  expect(within(danger).queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
  await actor.click(within(danger).getByRole('button', { name: 'Удалить аккаунт' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith(2))
  expect(await screen.findByText('Аккаунт удалён')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть аккаунт mechanic-two' })).not.toBeInTheDocument()
})

it.each(['selection', 'owner'])('does not apply a late account PATCH draft after %s changes', async change => {
  const actor = userEvent.setup()
  const other = { ...account, id: 3, username: 'mechanic-three', tracker_login: 'other.login' }
  vi.mocked(api.adminUsers).mockResolvedValue([account, other])
  let resolve!: (updated: AdminUser) => void
  const pending = new Promise<AdminUser>(done => { resolve = done })
  vi.spyOn(api, 'updateAdminUser').mockReturnValue(pending)
  const view = openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  await actor.click(within(detail).getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(api.updateAdminUser).toHaveBeenCalledTimes(1))
  if (change === 'owner') view.rerenderAuth(testUser({ id: 200, role: 'royal', permissions: ['users.manage'], parks: [north, south] }))
  await actor.click(await screen.findByRole('button', { name: 'Открыть аккаунт mechanic-three' }))
  const currentDetail = screen.getByRole('region', { name: 'Детали' })
  expect(within(currentDetail).getByLabelText('Tracker login')).toHaveValue('other.login')
  if (change === 'selection') vi.mocked(api.adminUsers).mockResolvedValue([{ ...account, tracker_login: 'saved.first' }, other])
  await act(async () => resolve({ ...account, tracker_login: 'saved.first' }))
  expect(within(currentDetail).getByLabelText('Tracker login')).toHaveValue('other.login')
  expect(screen.queryByText('Изменения сохранены')).not.toBeInTheDocument()
  await actor.click(screen.getByRole('button', { name: 'Открыть аккаунт mechanic-two' }))
  expect(within(screen.getByRole('region', { name: 'Детали' })).getByLabelText('Tracker login')).toHaveValue(change === 'selection' ? 'saved.first' : '')
})


it('refreshes the account list automatically without replacing an unsaved account draft', async () => {
  openAccounts()
  const detail = await screen.findByRole('region', { name: 'Детали' })
  fireEvent.change(within(detail).getByLabelText('Tracker login'), { target: { value: 'unsaved-login' } })
  vi.mocked(api.adminUsers).mockResolvedValue([{ ...account, username: 'renamed-elsewhere', tracker_login: 'server-login' }])
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByRole('button', { name: 'Открыть аккаунт renamed-elsewhere' })
  expect(within(detail).getByLabelText('Tracker login')).toHaveValue('unsaved-login')
  expect(api.adminUsers).toHaveBeenCalledTimes(2)
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
