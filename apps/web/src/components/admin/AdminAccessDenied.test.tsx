import { beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError, type AdminRole, type AdminUser, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { AdminRolesPanel } from './AdminRolesPanel'
import { AdminUsersPanel } from './AdminUsersPanel'
import { adminAccessDeniedMessage } from './adminResources'

const actor: User = { id: 1, username: 'owner', role: 'royal', access_status: 'approved', permissions: ['users.manage', 'roles.manage'], parks: [] }
const role: AdminRole = { id: 2, slug: 'lead', name: 'Закрытая роль', description: '', is_system: false, is_active: true, permissions: [], user_count: 1 }
const account: AdminUser = { id: 2, username: 'private-account', role: 'lead', role_id: 2, access_status: 'approved', is_active: true, parks: [], permissions: [], role_permissions: [] }
function tree(panel: 'users' | 'roles', principal = actor) {
  return <AuthContext.Provider value={{ user: principal, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>
    {panel === 'users' ? <AdminUsersPanel parks={[]} /> : <AdminRolesPanel />}
  </AuthContext.Provider>
}

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it.each([['users', 401], ['users', 403], ['roles', 401], ['roles', 403]] as const)(
  'retires the %s workspace after background %s and resumes only for another actor scope', async (panel, status) => {
    const users = vi.spyOn(api, 'adminUsers').mockResolvedValue([account])
    const roles = vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
    vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
    const mounted = render(tree(panel))
    const rowName = panel === 'users' ? /private-account/ : /Открыть роль Закрытая роль/
    fireEvent.click(await screen.findByRole('button', { name: rowName }))
    const fieldName = panel === 'users' ? 'Tracker login' : 'Название'
    fireEvent.change(screen.getAllByLabelText(fieldName)[0], { target: { value: 'private-draft' } })
    const read = panel === 'users' ? users : roles
    read.mockRejectedValue(new ApiError(status, 'access_denied'))
    const now = Date.now()
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 120_001)
    fireEvent.focus(window)
    await waitFor(() => expect(screen.getByText(adminAccessDeniedMessage)).toBeVisible())
    expect(screen.queryByRole('button', { name: rowName })).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('private-draft')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
    const attempts = read.mock.calls.length
    users.mockResolvedValue([account]); roles.mockResolvedValue([role])
    clock.mockReturnValue(now + 480_001)
    fireEvent.focus(window)
    fireEvent(window, new Event('online'))
    mounted.rerender(tree(panel))
    expect(read).toHaveBeenCalledTimes(attempts)
    expect(screen.getByText(adminAccessDeniedMessage)).toBeVisible()
    mounted.rerender(tree(panel, { ...actor, id: 3 }))
    await screen.findByRole('button', { name: rowName })
    await waitFor(() => expect(read).toHaveBeenCalledTimes(attempts + 1))
  },
)


it.each(['users', 'roles'] as const)('keeps the %s draft available after a transient background failure', async panel => {
  const users = vi.spyOn(api, 'adminUsers').mockResolvedValue([account])
  const roles = vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
  render(tree(panel))
  const rowName = panel === 'users' ? /private-account/ : /Открыть роль Закрытая роль/
  fireEvent.click(await screen.findByRole('button', { name: rowName }))
  const fieldName = panel === 'users' ? 'Tracker login' : 'Название'
  fireEvent.change(screen.getAllByLabelText(fieldName)[0], { target: { value: 'keep-my-draft' } })
  const read = panel === 'users' ? users : roles
  read.mockRejectedValue(new ApiError(503, 'temporarily_unavailable'))
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2))
  await screen.findByRole('alert')
  expect(screen.getByDisplayValue('keep-my-draft')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeEnabled()
  expect(screen.queryByText(adminAccessDeniedMessage)).not.toBeInTheDocument()
})


it('retires a pending background role read before refreshing after a saved mutation', async () => {
  const roles = vi.spyOn(api, 'adminRoles').mockResolvedValue([role])
  vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
  const saved = { ...role, name: 'Сохранённая роль' }
  vi.spyOn(api, 'updateAdminRole').mockResolvedValue(saved)
  render(tree('roles'))
  fireEvent.click(await screen.findByRole('button', { name: /Открыть роль Закрытая роль/ }))
  fireEvent.change(screen.getByLabelText('Название'), { target: { value: saved.name } })
  let finishBackground!: (rows: AdminRole[]) => void
  roles.mockImplementationOnce(() => new Promise(resolve => { finishBackground = resolve })).mockResolvedValue([saved])
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(roles).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await screen.findByRole('button', { name: 'Открыть роль Сохранённая роль' })
  expect(roles).toHaveBeenCalledTimes(3)
  await act(async () => { finishBackground([role]) })
  expect(screen.getByRole('button', { name: 'Открыть роль Сохранённая роль' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть роль Закрытая роль' })).not.toBeInTheDocument()
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
