import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AuthContext } from '../../auth-context'
import { ApiError, type User } from '../../api'
import { resourceStore } from '../../lib/resource'
import { HostHealthPanel } from './HostHealthPanel'
import { hostHealthApi, type HostHealth } from './hostHealthApi'

const actor: User = { id: 1, username: 'owner', role: 'royal', access_status: 'approved', permissions: ['nav.admin'], parks: [] }
const snapshot: HostHealth = { sampled_at: 1000, database: 'ok', window_seconds: 300, disk: { free_bytes: 1024, total_bytes: 100000 }, memory: { total_bytes: null, available_bytes: null, container_limit_bytes: null, container_used_bytes: null }, backup: { verified_at: null, overdue: false, last_attempt_failed: false }, requests: { tracker: { requests: 3, errors: 1, limited: 1, average_ms: 120, max_ms: 200 } } }
function tree(user = actor) { return <AuthContext.Provider value={{ user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><HostHealthPanel /></AuthContext.Provider> }
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks() })

it('shows observed warnings and distinguishes unavailable metrics from zero', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue(snapshot)
  render(tree())
  expect(await screen.findByText(/Мало свободного места/)).toBeVisible()
  expect(screen.getByText(/Проверенная копия ещё не отмечена/)).toBeVisible()
  expect(screen.getAllByText(/Нет данных/).length).toBeGreaterThan(0)
  expect(screen.getByText(/Есть ошибки ответов/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /обновить/i })).not.toBeInTheDocument()
})

it('does not request privileged metrics for a mechanic', async () => {
  const read = vi.spyOn(hostHealthApi, 'get')
  render(tree({ ...actor, role: 'mechanic', permissions: [] }))
  await waitFor(() => expect(screen.getByText(/Доступ к состоянию сервера закрыт/)).toBeVisible())
  expect(read).not.toHaveBeenCalled()
})

it('hides metrics on access denial', async () => {
  vi.spyOn(hostHealthApi, 'get').mockRejectedValue(new ApiError(403))
  render(tree())
  expect(await screen.findByText(/Доступ к состоянию сервера закрыт/)).toBeVisible()
  expect(screen.queryByText('SQLite')).not.toBeInTheDocument()
})
