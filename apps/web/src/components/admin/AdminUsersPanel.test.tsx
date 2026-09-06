import { resourceStore } from '../../lib/resource'
import { render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api, type AdminRole, type AdminUser, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { AdminUsersPanel } from './AdminUsersPanel'

const actor: User = {
  id: 1,
  username: 'manager',
  role: 'field_lead',
  access_status: 'approved',
  permissions: ['users.manage'],
  parks: [],
}

const roles: AdminRole[] = [
  { id: 1, slug: 'mechanic', name: 'Механик', description: '', is_system: true, is_active: true, permissions: ['reports.create'], user_count: 0 },
  { id: 2, slug: 'dispatch_lead', name: 'Старший диспетчер', description: '', is_system: false, is_active: true, permissions: ['nav.admin'], user_count: 1 },
]

const privilegedUser: AdminUser = {
  id: 2,
  username: 'privileged-custom',
  role: 'dispatch_lead',
  role_id: 2,
  access_status: 'approved',
  is_active: true,
  parks: [],
  permissions: ['nav.admin'],
  role_permissions: ['nav.admin'],
}

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('hides custom privileged roles and locks an existing privileged identity for a non-owner', async () => {
  vi.spyOn(api, 'adminUsers').mockResolvedValue([privilegedUser])
  vi.spyOn(api, 'adminRoles').mockResolvedValue(roles)
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])

  render(
    <AuthContext.Provider value={{
      user: actor,
      loading: false,
      login: vi.fn(),
      refreshUser: vi.fn(),
      logout: vi.fn(),
    }}>
      <AdminUsersPanel parks={[]} />
    </AuthContext.Provider>,
  )

  expect(await screen.findByText('Аккаунт с привилегированной ролью может менять только владелец.')).toBeVisible()
  expect(screen.queryByRole('option', { name: 'Старший диспетчер' })).not.toBeInTheDocument()
  const createPanel = screen.getByRole('heading', { name: 'Создать пользователя' }).closest('section')
  expect(createPanel).not.toBeNull()
  expect(within(createPanel as HTMLElement).getByRole('option', { name: 'Механик' })).toBeVisible()
  expect(within(createPanel as HTMLElement).getByRole('button', { name: 'Создать' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
})
