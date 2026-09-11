import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { AdminRolesPanel } from './AdminRolesPanel'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); resourceStore.clearAll() })

it('does not mount a role editor before explicit selection', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.spyOn(api, 'adminRoles').mockResolvedValue([{ id: 1, slug: 'mechanic', name: 'Механик', description: '', is_system: true, is_active: true, permissions: [], user_count: 0 }])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
  const actor = { id: 1, username: 'admin', role: 'admin', access_status: 'approved', permissions: ['roles.manage'], parks: [] } as User
  render(<AuthContext.Provider value={{ user: actor, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}><AdminRolesPanel /></AuthContext.Provider>)
  expect(await screen.findByRole('button', { name: 'Открыть роль Механик' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Новая роль' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Название')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Открыть роль Механик' }))
  expect(screen.getByRole('heading', { name: 'Редактор: Механик' })).toBeVisible()
})
