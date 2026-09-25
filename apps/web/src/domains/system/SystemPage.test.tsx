import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../../auth-context'
import { ApiError, type User } from '../../api'
import { SystemPage } from './SystemPage'
import type { HostCapabilities, SystemClient, SystemJob, SystemSummary } from '../../opsApi'
import { readOperationReservation, writeOperationReservation } from './operationReservation'

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
    getOperation: vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found')),
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
    expect(operation[0]).toEqual({ operation_id: reauth.operation_id, kind: 'diagnostics', capability_revision: revision, confirmation: 'ЗАПУСТИТЬ DIAGNOSTICS' })
    expect(operation[1]).toBe('reauth-token')
    expect(readOperationReservation()?.id).toBe(reauth.operation_id)
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

  it('closes and clears an open confirmation when a background refresh changes the revision', async () => {
    const revisionB = 'b'.repeat(64)
    const getCapabilities = vi.fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValue({ ...capabilities, revision: revisionB })
    const api = client({ getCapabilities })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })

    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getCapabilities).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Подтвердить операцию' })).not.toBeInTheDocument())
    expect(api.reauthorize).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Собрать диагностику' }))
    const reopened = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    expect(within(reopened).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS')).toHaveValue('')
    expect(within(reopened).getByLabelText('Пароль')).toHaveValue('')
    expect(within(reopened).getByLabelText('Код TOTP или восстановления')).toHaveValue('')
  })

  it.each([
    ['lost response', new Error('response lost')],
    ['dispatch conflict', new ApiError(409, 'host_work_in_progress')],
  ])('persists before POST and reconciles the exact UUID after %s', async (_label, failure) => {
    let submittedId = ''
    const startOperation = vi.fn().mockImplementation(async payload => {
      submittedId = payload.operation_id
      expect(readOperationReservation()?.id).toBe(submittedId)
      throw failure
    })
    const api = client({ startOperation })
    const first = render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Подтвердить операцию' })).not.toBeInTheDocument())
    expect(readOperationReservation()?.id).toBe(submittedId)
    first.unmount()

    const resumed = client({
      getOperation: vi.fn().mockResolvedValue({ id: submittedId, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 25, error: null }),
      startOperation,
    })
    render(tree('royal', resumed))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '25')
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    expect(startOperation).toHaveBeenCalledOnce()
  })

  it('does not reconcile a reserved UUID while its POST is still unresolved', async () => {
    let release: ((value: SystemJob) => void) | undefined
    const getOperation = vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found'))
    const startOperation = vi.fn().mockImplementation(() => new Promise<SystemJob>(resolve => { release = resolve }))
    const api = client({ getOperation, startOperation })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledOnce())

    fireEvent(window, new Event('focus'))
    await act(async () => { await Promise.resolve() })
    expect(getOperation).not.toHaveBeenCalled()
    expect(readOperationReservation()).not.toBeNull()

    release?.({ id: 'accepted', kind: 'diagnostics', state: 'running', phase: 'awaiting_host', progress_percent: 0, error: null })
  })

  it('keeps an unknown reservation locked, then clears it only after authoritative exact 404', async () => {
    let submittedId = ''
    const getOperation = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new ApiError(404, 'operation_not_found'))
    const startOperation = vi.fn().mockImplementation(async payload => {
      submittedId = payload.operation_id
      throw new Error('request outcome unknown')
    })
    const api = client({ getOperation, startOperation })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    expect(await screen.findByText('Проверяем получение запроса')).toBeVisible()
    expect(readOperationReservation()?.id).toBe(submittedId)
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()

    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getOperation).toHaveBeenCalledWith(submittedId))
    expect(readOperationReservation()?.id).toBe(submittedId)
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    expect(startOperation).toHaveBeenCalledOnce()

    const reservation = readOperationReservation()
    if (reservation) writeOperationReservation({ ...reservation, created_at: 0 })
    fireEvent(window, new Event('focus'))
    expect(await screen.findByText('Запрос не получен')).toBeVisible()
    await waitFor(() => expect(localStorage.getItem('robopark:system-operation')).toBeNull())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeEnabled())
    expect(startOperation).toHaveBeenCalledOnce()
  })

  it('disables operations exactly when the capability snapshot expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-25T09:00:00Z'))
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue({ ...capabilities, expires_at: '2026-09-25T09:00:01Z' }),
    })
    render(tree('royal', api))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeEnabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(1_001) })
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    expect(api.getCapabilities).toHaveBeenCalledOnce()
  })

  it('resumes progress by stored operation UUID after reload', async () => {
    writeOperationReservation({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null }) })
    render(tree('royal', api))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '50')
    expect(screen.getByText('11111111-1111-4111-8111-111111111111')).toBeVisible()
  })

  it('does not query or adopt an operation without an exact locally stored UUID', async () => {
    const api = client()
    render(tree('royal', api))
    await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(screen.queryByRole('progressbar', { name: 'Прогресс операции' })).not.toBeInTheDocument()
    expect(localStorage.getItem('robopark:system-operation')).toBeNull()
    expect(api.getOperation).not.toHaveBeenCalled()
  })

  it('requires an exact sanitized discovered USB UUID before enabling selection', async () => {
    localStorage.setItem('robopark:system-operation', '33333333-3333-4333-8333-333333333333')
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id: '33333333-3333-4333-8333-333333333333', kind: 'usb-discover', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { devices: [{ device_uuid: '44444444-4444-4444-8444-444444444444', removable: true, mounted: false }] },
    }) })
    render(tree('royal', api))
    const selectButton = await screen.findByRole('button', { name: 'Выбрать USB' })
    expect(selectButton).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: 'Обнаруженное USB-устройство' }), { target: { value: '44444444-4444-4444-8444-444444444444' } })
    expect(selectButton).toBeEnabled()
    fireEvent.click(selectButton)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ USB-SELECT'), { target: { value: 'ЗАПУСТИТЬ USB-SELECT' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    expect(vi.mocked(api.startOperation).mock.calls[0][0]).toMatchObject({
      kind: 'usb-select', device_uuid: '44444444-4444-4444-8444-444444444444', confirmation: 'ЗАПУСТИТЬ USB-SELECT',
    })
  })

  it('labels partial telemetry as unknown and exposes a textual seven-day history', async () => {
    const partial: SystemSummary = {
      ...summary,
      sync: { ...summary.sync, last_error: null, worker_lease_state: 'unknown' },
      metrics: { host: {} },
    }
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue(partial),
      getHistory: vi.fn().mockResolvedValue({ active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] }),
    })))
    const table = await screen.findByRole('table', { name: 'Активные пользователи за 7 дней — значения' })
    expect(within(table).getByText('2026-09-24')).toBeVisible()
    expect(within(table).getByText('6')).toBeVisible()
    expect(screen.queryByText('Резервная копия: проверена')).not.toBeInTheDocument()
    expect(screen.queryByText('Очистка: без ошибок')).not.toBeInTheDocument()
    expect(screen.getByText('Резервная копия: Неизвестно')).toBeVisible()
    expect(screen.getByText('Очистка: Неизвестно')).toBeVisible()
    expect(screen.getAllByText('Нет данных').length).toBeGreaterThanOrEqual(6)
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

  it.each([401, 403])('removes protected system data when a refresh returns %s', async status => {
    const getSummary = vi.fn()
      .mockResolvedValueOnce(summary)
      .mockRejectedValueOnce(new ApiError(status, 'forbidden'))
    const api = client({ getSummary })
    render(tree('admin', api))
    expect(await screen.findByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getSummary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument())
  })
})
