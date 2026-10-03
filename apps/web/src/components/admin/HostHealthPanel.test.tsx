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

it('names failed disk measurement and missing host agent instead of only showing no data', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    host_health_source_state: 'unavailable',
    disk: { total_bytes: null, free_bytes: null, source_state: 'unavailable' },
  })
  render(tree())

  expect(await screen.findByText(/Не удалось измерить диск API/)).toBeVisible()
  expect(screen.getByText(/Снимок host agent отсутствует/)).toBeVisible()
  expect(screen.getAllByText('Нет данных').length).toBeGreaterThan(0)
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

it('shows bounded storage, leak observations and selected acceleration', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    storage: { floor_bytes: 6 * 1024 ** 3, bytes_to_reclaim: 0, category_bytes: { cache: 1024 }, last_cleanup_at: 900, cleanup_failed: false },
    process: { rss_bytes: 2 * 1024 ** 3, rss_trend_bytes: 1024, open_fds: 21, tasks: 5, threads: 6, cache_bytes: 1024, db_pool_checked_out: 2, memory_pressure: { sustained: true, evicted: false, failed: true } },
    capabilities: { profile: 'orin', jpeg_backend: 'nvjpeg', hardware_jpeg: true, npu_available: false, cuda_available: true },
  })
  render(tree())
  expect(await screen.findByText(/Профиль: orin/)).toBeVisible()
  expect(screen.getByText(/JPEG: nvjpeg/)).toBeVisible()
  expect(screen.getByText(/Последняя уборка/)).toBeVisible()
  expect(screen.getByText(/RSS процесса/)).toBeVisible()
  expect(screen.getByText(/Давление памяти сохраняется/)).toBeVisible()
})

it('identifies an invalid host capability report instead of presenting a fallback as measured hardware', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    capabilities: { profile: 'generic-arm', jpeg_backend: 'software', hardware_jpeg: false, npu_available: false, cuda_available: false, source_state: 'invalid' },
  })
  render(tree())

  expect(await screen.findByText(/Повреждены сведения о возможностях хоста/)).toBeVisible()
  expect(screen.queryByText(/Профиль: generic-arm/)).not.toBeInTheDocument()
})

it('warns when a previously measured host profile is stale', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    capabilities: { profile: 'orin', jpeg_backend: 'software', hardware_jpeg: false, npu_available: false, cuda_available: false, source_state: 'stale' },
  })
  render(tree())

  expect(await screen.findByText(/Проверка аппаратных возможностей хоста устарела/)).toBeVisible()
  expect(screen.queryByText(/Профиль: orin/)).not.toBeInTheDocument()
})

it('shows remaining disk pressure separately from cleanup failure', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    storage: { floor_bytes: 6 * 1024 ** 3, bytes_to_reclaim: null, category_bytes: {}, last_cleanup_at: 900, cleanup_failed: false, space_pressure: true },
    process: { rss_bytes: null, rss_trend_bytes: 0, open_fds: null, tasks: 0, threads: 0, cache_bytes: 0, db_pool_checked_out: null },
  })
  render(tree())
  expect(await screen.findByText(/Автоочистка завершилась, но свободного места всё ещё недостаточно/)).toBeVisible()
  expect(screen.getAllByText('Нет данных').length).toBeGreaterThan(0)
  expect(screen.queryByText(/Автоочистка не завершилась/)).not.toBeInTheDocument()
})

it('shows host cleanup deferred by a current operation without calling it a failure', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    storage: { floor_bytes: 6 * 1024 ** 3, bytes_to_reclaim: 0, category_bytes: {}, last_cleanup_at: 900, cleanup_failed: false, cleanup_busy: true },
    process: { rss_bytes: null, rss_trend_bytes: 0, open_fds: null, tasks: 0, threads: 0, cache_bytes: 0, db_pool_checked_out: null },
  })
  render(tree())

  expect(await screen.findByText(/Автоочистка отложена до завершения текущей операции/)).toBeVisible()
  expect(screen.queryByText(/Автоочистка не завершилась/)).not.toBeInTheDocument()
})

it('keeps cleanup errors ahead of a simultaneous busy marker', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    storage: { floor_bytes: 6 * 1024 ** 3, bytes_to_reclaim: 1, category_bytes: {}, last_cleanup_at: 900, cleanup_failed: true, cleanup_busy: true, api_cleanup_failed: true },
    process: { rss_bytes: null, rss_trend_bytes: 0, open_fds: null, tasks: 0, threads: 0, cache_bytes: 0, db_pool_checked_out: null },
  })
  render(tree())

  expect(await screen.findByText(/Автоочистка не завершилась/)).toBeVisible()
  expect(screen.queryByText(/Автоочистка отложена/)).not.toBeInTheDocument()
})

it('reports low disk during deferred cleanup without claiming cleanup completed', async () => {
  vi.spyOn(hostHealthApi, 'get').mockResolvedValue({
    ...snapshot,
    storage: { floor_bytes: 6 * 1024 ** 3, bytes_to_reclaim: 1024, category_bytes: {}, last_cleanup_at: 900, cleanup_failed: false, cleanup_busy: true, space_pressure: true },
    process: { rss_bytes: null, rss_trend_bytes: 0, open_fds: null, tasks: 0, threads: 0, cache_bytes: 0, db_pool_checked_out: null },
  })
  render(tree())

  expect(await screen.findByText(/Автоочистка отложена до завершения текущей операции/)).toBeVisible()
  expect(screen.getByText(/свободного места.*недостаточно/i)).toBeVisible()
  expect(screen.queryByText(/Автоочистка завершилась/)).not.toBeInTheDocument()
})
