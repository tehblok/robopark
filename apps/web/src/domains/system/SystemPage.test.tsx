import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../../auth-context'
import { ApiError, type User } from '../../api'
import { SystemPage } from './SystemPage'
import type { HostCapabilities, SystemClient, SystemSummary } from '../../opsApi'

const revision = 'a'.repeat(64)
const kinds = [
  'release-update', 'reinstall', 'rollback', 'package-inspect', 'package-update',
  'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore',
  'cleanup-preview', 'cleanup-execute', 'diagnostics', 'usb-discover', 'usb-format',
  'usb-select',
] as const
const safe = new Set(['package-inspect', 'backup-verify', 'cleanup-preview', 'diagnostics', 'usb-discover', 'usb-select'])

const summary: SystemSummary = {
  sampled_at: '2026-09-25T09:00:00Z', metrics_stale: false,
  online: { total: 4, by_role: { mechanic: 3, admin: 1 }, by_park: { '7': 4 } },
  sync: { cursor_age_seconds: 12, pending_action_count: 2, oldest_pending_action_age_seconds: 20, retry_count: 1, needs_attention_count: 1, last_success_at: '2026-09-25T08:59:00Z', last_error: null, worker_lease_state: 'active' },
  push: { pending: 1, needs_attention: 0 },
  metrics: { host: {
    cpu: { state: 'ok', load_1m: 0.4, cores: 4 },
    disk: { total_bytes: 1000, free_bytes: 400 },
    memory: { total_bytes: 1000, available_bytes: 500, container_limit_bytes: 800, container_used_bytes: 300 },
    postgresql: { state: 'ok' }, container: { state: 'ok' }, tuna: { state: 'degraded' }, internet: { state: 'ok' },
    requests: { tracker: { requests: 3, errors: 1, limited: 0, average_ms: 100, max_ms: 200 } },
    storage: { floor_bytes: 100, bytes_to_reclaim: 10, category_bytes: { logs: 20 }, last_cleanup_at: 1, cleanup_failed: false },
    backup: { verified_at: 1, overdue: false, last_attempt_failed: false },
  } },
  release: { version: '0.2.0-rc.6', cleanup: { state: 'ready' } },
}

const capabilities: HostCapabilities = {
  state: 'ready', generated_at: '2026-09-25T09:00:00Z', expires_at: '2099-09-25T09:05:00Z', revision,
  operations: Object.fromEntries(kinds.map(kind => [kind, safe.has(kind)
    ? { available: true, unavailable_reason: null }
    : { available: false, unavailable_reason: 'capability_unavailable' }])) as HostCapabilities['operations'],
}

function client(overrides: Partial<SystemClient> = {}): SystemClient {
  return {
    getSummary: vi.fn().mockResolvedValue(summary),
    getHistory: vi.fn().mockResolvedValue({ active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] }),
    getCapabilities: vi.fn().mockResolvedValue(capabilities),
    getJob: vi.fn().mockResolvedValue({ id: '', kind: '', state: 'idle', phase: '', progress_percent: null, error: null }),
    reauthorize: vi.fn().mockResolvedValue({ token: 'reauth-token', expires_in: 120 }),
    startOperation: vi.fn().mockImplementation(async payload => ({ id: payload.operation_id, kind: payload.kind, state: 'running', phase: 'accepted', progress_percent: 0, error: null })),
    ...overrides,
  }
}

const user = (role: 'admin' | 'royal'): User => ({
  id: 1, username: role, role, access_status: 'approved', permissions: ['nav.admin'], parks: [],
})
function tree(role: 'admin' | 'royal', api = client()) {
  return <MemoryRouter><AuthContext.Provider value={{ user: user(role), loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><SystemPage client={api} /></AuthContext.Provider></MemoryRouter>
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear() })

describe('SystemPage', () => {
  it('shows scoped current and seven-day health to admin without loading or mutating host operations', async () => {
    const api = client()
    render(tree('admin', api))
    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText('4')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    for (const label of ['CPU', 'Память', 'Диск', 'PostgreSQL', 'Контейнеры', 'Сеть', 'Wi‑Fi', 'Очередь', 'Worker', 'Tracker', 'Tuna', 'Хранилище']) {
      expect(screen.getByText(label)).toBeVisible()
    }
    expect(screen.getByRole('link', { name: 'Открыть ошибки синхронизации' })).toHaveAttribute('href', '/work?sync=needs_attention')
    expect(api.getCapabilities).not.toHaveBeenCalled()
    expect(api.reauthorize).not.toHaveBeenCalled()
    expect(api.startOperation).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox', { name: /команд|argv/i })).not.toBeInTheDocument()
  })

  it('shows only the live six as actionable and explains every unavailable kind', async () => {
    render(tree('royal'))
    const operations = await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(within(operations).getByRole('button', { name: 'Предпросмотр очистки' })).toBeEnabled()
    expect(within(operations).getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
    expect(within(operations).getAllByText('Недоступно на этом хосте').length).toBe(10)
    expect(within(operations).queryByLabelText(/команд|argv/i)).not.toBeInTheDocument()
  })

  it('fails closed when a previously ready capability snapshot has expired', async () => {
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue({
        ...capabilities, expires_at: '2020-01-01T00:00:00Z',
      }),
    })
    render(tree('royal', api))
    expect(await screen.findByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Собрать диагностику' }))
    expect(api.reauthorize).not.toHaveBeenCalled()
  })

  it('requires exact typed confirmation and password plus TOTP or recovery code, binding both calls to the revision', async () => {
    const api = client()
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    const submit = within(dialog).getByRole('button', { name: 'Запустить' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS ' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.click(submit)
    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    const reauth = vi.mocked(api.reauthorize).mock.calls[0][0]
    const operation = vi.mocked(api.startOperation).mock.calls[0]
    expect(reauth).toMatchObject({ operation_kind: 'diagnostics', capability_revision: revision, password: 'secret', code: '123456' })
    expect(reauth.operation_id).toMatch(/^[a-f0-9-]{36}$/)
    expect(operation[0]).toEqual({ operation_id: reauth.operation_id, kind: 'diagnostics', capability_revision: revision })
    expect(operation[1]).toBe('reauth-token')
    expect(localStorage.getItem('robopark:system-operation')).toBe(reauth.operation_id)
  })

  it('refreshes capabilities and never submits after revision drift', async () => {
    const api = client({ reauthorize: vi.fn().mockRejectedValue(new ApiError(409, 'capabilities_changed')) })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    expect(await within(dialog).findByText('Возможности хоста изменились. Список обновлён; подтвердите операцию заново.')).toBeVisible()
    expect(api.getCapabilities).toHaveBeenCalledTimes(2)
    expect(api.startOperation).not.toHaveBeenCalled()
    expect(within(dialog).getByRole('button', { name: 'Запустить' })).toBeDisabled()
  })

  it('resumes progress by stored operation UUID after reload', async () => {
    localStorage.setItem('robopark:system-operation', '11111111-1111-4111-8111-111111111111')
    const api = client({ getJob: vi.fn().mockResolvedValue({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null }) })
    render(tree('royal', api))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '50')
    expect(screen.getByText('11111111-1111-4111-8111-111111111111')).toBeVisible()
  })

  it('pauses polling while hidden and resumes once without duplicate timers', async () => {
    vi.useFakeTimers()
    const api = client()
    render(tree('admin', api))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const initial = vi.mocked(api.getSummary).mock.calls.length
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial)
    hidden.mockReturnValue(false)
    fireEvent(document, new Event('visibilitychange'))
    fireEvent(window, new Event('focus'))
    fireEvent(window, new Event('focus'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial + 1)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial + 2)
  })

  it('removes protected system data when a refresh is denied', async () => {
    const getSummary = vi.fn()
      .mockResolvedValueOnce(summary)
      .mockRejectedValueOnce(new ApiError(403, 'forbidden'))
    const api = client({ getSummary })
    render(tree('admin', api))
    expect(await screen.findByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getSummary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument())
  })
})
