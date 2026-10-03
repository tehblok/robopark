import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { ApiError, type ScheduleEntry, type ScheduleParticipant, type User } from '../../api'
import { activateDeviceResourceCache, currentDeviceResourceCache, purgeDeviceResourceCache } from '../../lib/deviceResourceCache'
import { resourceStore } from '../../lib/resource'
import { offlineScopeForUser } from '../../lib/deviceResourceCache'
import { SyncContextProvider, type SyncContextValue } from '../../pwa/SyncProvider'
import type { OfflineAction } from '../../pwa/offlineTypes'
import { ScheduleWorkspace, type ScheduleApiClient } from './ScheduleWorkspace'

const park = { id: 1, name: 'Парк', timezone: 'Europe/Moscow', tag: 'PARK', is_active: true }
const mechanic: User = { id: 7, username: 'mech', role: 'mechanic', access_status: 'approved', parks: [park] }
const entry: ScheduleEntry = { id: 'one', owner_user_id: 7, park_id: 1, kind: 'shift', start_at: '2026-09-21T09:00:00+03:00', end_at: '2026-09-21T21:00:00+03:00', source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [] }
afterEach(async () => { resourceStore.clearAll(); await purgeDeviceResourceCache(); vi.unstubAllGlobals() })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, reject, resolve }
}

function client(overrides: Partial<ScheduleApiClient> = {}): ScheduleApiClient {
  return { schedules: vi.fn(async () => [entry]), scheduleCreate: vi.fn(async () => entry), scheduleUpdate: vi.fn(async () => entry), scheduleDelete: vi.fn(async () => undefined), schedulePattern: vi.fn(async () => []), scheduleCopy: vi.fn(async () => []), scheduleParticipants: vi.fn(async () => []), ...overrides }
}

describe('ScheduleWorkspace', () => {
  it('stores a new shift locally and keeps its pending sync state visible', async () => {
    const queued: OfflineAction[] = []
    const enqueueAction = vi.fn(async (input: Parameters<SyncContextValue['enqueueAction']>[0]) => {
      const action: OfflineAction = { ...input, state: 'ready', attempts: 0, createdAt: Date.now(), updatedAt: Date.now() }
      queued.push(action)
      return action
    })
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 }, actionTrackingReady: true,
      scopeKey: JSON.stringify(offlineScopeForUser(mechanic, '1')),
      enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(),
      findAction: vi.fn(), subscribeAction: vi.fn(() => () => undefined), listActions: vi.fn(async () => queued),
    } satisfies SyncContextValue
    const apiClient = client({ schedules: vi.fn(async () => []), scheduleCreate: vi.fn(async () => entry) })
    const initialAnchor = new Date('2026-09-21T12:00:00+03:00')
    const first = render(<SyncContextProvider value={sync}><ScheduleWorkspace apiClient={apiClient} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-21T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-21T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    expect(await screen.findByText('Моя смена')).toBeVisible()
    expect(screen.getByText('Ожидает синхронизации')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Изменить' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Удалить' })).not.toBeInTheDocument()
    expect(apiClient.scheduleCreate).not.toHaveBeenCalled()
    expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({
      resourceType: 'schedule_entry', action: 'schedule_create', payload: expect.objectContaining({ park_id: 1 }),
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-21T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-21T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    expect(await screen.findByText('Такой период уже сохранён на устройстве.')).toBeVisible()
    expect(enqueueAction).toHaveBeenCalledTimes(1)
    first.unmount()
    render(<SyncContextProvider value={sync}><ScheduleWorkspace apiClient={apiClient} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    expect(screen.getByText('Ожидает синхронизации')).toBeVisible()
  })

  it('queues an edit with the server revision and blocks editing until it is confirmed', async () => {
    const queued: OfflineAction[] = []
    const enqueueAction = vi.fn(async (input: Parameters<SyncContextValue['enqueueAction']>[0]) => {
      const action: OfflineAction = { ...input, state: 'ready', createdAt: Date.now(), updatedAt: Date.now() }
      queued.push(action)
      return action
    })
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 }, actionTrackingReady: true,
      scopeKey: JSON.stringify(offlineScopeForUser(mechanic, '1')),
      enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(),
      findAction: vi.fn(), subscribeAction: vi.fn(() => () => undefined), listActions: vi.fn(async () => queued),
    } satisfies SyncContextValue
    const apiClient = client()
    render(<SyncContextProvider value={sync}><ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
    fireEvent.change(screen.getByLabelText('Тип'), { target: { value: 'vacation' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    expect(await screen.findByText('Мой отпуск')).toBeVisible()
    expect(screen.getByText('Ожидает синхронизации')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Изменить' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Удалить' })).not.toBeInTheDocument()
    expect(apiClient.scheduleUpdate).not.toHaveBeenCalled()
    expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'schedule_update', resourceId: entry.id, baseRevision: entry.updated_at,
    }))
  })

  it('queues a deletion and restores the entry if the server rejects it', async () => {
    const queued: OfflineAction[] = []
    const enqueueAction = vi.fn(async (input: Parameters<SyncContextValue['enqueueAction']>[0]) => {
      const action: OfflineAction = { ...input, state: 'ready', createdAt: Date.now(), updatedAt: Date.now() }
      queued.push(action)
      return action
    })
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 }, actionTrackingReady: true,
      scopeKey: JSON.stringify(offlineScopeForUser(mechanic, '1')),
      enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(),
      findAction: vi.fn(), subscribeAction: vi.fn(() => () => undefined), listActions: vi.fn(async () => queued),
    } satisfies SyncContextValue
    const apiClient = client()
    const view = render(<SyncContextProvider value={sync}><ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Удалить' }))
    await waitFor(() => expect(screen.queryByText('Моя смена')).not.toBeInTheDocument())
    expect(apiClient.scheduleDelete).not.toHaveBeenCalled()
    expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'schedule_delete', resourceId: entry.id, baseRevision: entry.updated_at,
    }))
    queued[0] = { ...queued[0], state: 'conflict' }
    view.rerender(<SyncContextProvider value={{ ...sync, state: { status: 'attention', pending: 1, conflicts: 1 } }}><ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    expect(screen.getByText(/Некоторые изменения графика не применены/)).toBeVisible()
  })

  it('queues a personal template and a copied period as compact batch actions', async () => {
    const queued: OfflineAction[] = []
    const enqueueAction = vi.fn(async (input: Parameters<SyncContextValue['enqueueAction']>[0]) => {
      const action: OfflineAction = { ...input, state: 'ready', createdAt: Date.now(), updatedAt: Date.now() }
      queued.push(action)
      return action
    })
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 }, actionTrackingReady: true,
      scopeKey: JSON.stringify(offlineScopeForUser(mechanic, '1')),
      enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(),
      findAction: vi.fn(), subscribeAction: vi.fn(() => () => undefined), listActions: vi.fn(async () => queued),
    } satisfies SyncContextValue
    const apiClient = client()
    render(<SyncContextProvider value={sync}><ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} /></SyncContextProvider>)
    fireEvent.click(await screen.findByRole('tab', { name: 'Шаблоны' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Создать смены' })).toBeEnabled())
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-09-22' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-09-22' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))
    await waitFor(() => expect(enqueueAction).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/синхрон/i)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Копировать с'), { target: { value: '2026-09-21T00:00' } })
    fireEvent.change(screen.getByLabelText('Копировать по'), { target: { value: '2026-09-22T00:00' } })
    fireEvent.change(screen.getByLabelText('Начало копии'), { target: { value: '2026-09-28T00:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Копировать период' }))
    await waitFor(() => expect(enqueueAction).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/синхрон/i)).not.toBeInTheDocument()
    expect(enqueueAction.mock.calls.map(call => call[0].action)).toEqual(['schedule_pattern', 'schedule_copy'])
    expect(apiClient.schedulePattern).not.toHaveBeenCalled()
    expect(apiClient.scheduleCopy).not.toHaveBeenCalled()
  })
  it('keeps the last authorized shift visible when the route reopens without Wi-Fi', async () => {
    const initialAnchor = new Date('2026-09-21T12:00:00+03:00')
    const first = render(<ScheduleWorkspace apiClient={client()} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    first.unmount()

    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => { throw new TypeError('Failed to fetch') }) })} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    expect(await screen.findByText('Не удалось обновить график')).toBeVisible()
  })

  it('restores the scoped schedule from device storage after an offline page restart', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    await activateDeviceResourceCache(mechanic, '1')
    const initialAnchor = new Date('2026-09-21T12:00:00+03:00')
    const first = render(<ScheduleWorkspace apiClient={client()} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    await waitFor(async () => expect((await currentDeviceResourceCache()!.stats()).entries).toBeGreaterThan(0))
    first.unmount()
    resourceStore.clearAll()

    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => { throw new TypeError('Failed to fetch') }) })} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    expect(await screen.findByText('Не удалось обновить график')).toBeVisible()
  })

  it('does not replace a fresh server schedule with a late device snapshot', async () => {
    const snapshot = deferred<ScheduleEntry[] | undefined>()
    const hydrate = vi.spyOn(resourceStore, 'hydrate').mockReturnValue(snapshot.promise)
    const fresh = { ...entry, kind: 'vacation' as const, updated_at: '2026-09-21T10:00:00Z' }
    const stale = { ...entry, kind: 'sick' as const }
    try {
      render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => [fresh]) })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
      expect(await screen.findByText('Мой отпуск')).toBeVisible()

      await act(async () => { snapshot.resolve([stale]); await snapshot.promise })

      expect(screen.getByText('Мой отпуск')).toBeVisible()
      expect(screen.queryByText('Моя болезнь')).not.toBeInTheDocument()
    } finally {
      hydrate.mockRestore()
    }
  })

  it('keeps a confirmed schedule edit in the local view after reopening offline', async () => {
    const initialAnchor = new Date('2026-09-21T12:00:00+03:00')
    const updated = { ...entry, kind: 'vacation' as const }
    const first = render(<ScheduleWorkspace apiClient={client({ scheduleUpdate: vi.fn(async () => updated) })} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }))
    fireEvent.change(screen.getByLabelText('Тип'), { target: { value: 'vacation' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    expect(await screen.findByText('Мой отпуск')).toBeVisible()
    first.unmount()

    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => { throw new TypeError('Failed to fetch') }) })} initialAnchor={initialAnchor} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Мой отпуск')).toBeVisible()
  })
  it('shows personal calendar to mechanics and read-only team to admin', async () => {
    const mechanicView = render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByRole('tab', { name: 'Мой календарь' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Команда' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Добавить период' })).toBeVisible()
    mechanicView.unmount()

    render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, role: 'admin' }} />)
    expect(await screen.findByRole('tab', { name: 'Команда' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Мой календарь' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Добавить период' })).not.toBeInTheDocument()
  })

  it('gives royal access to personal, team and planning views', async () => {
    render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, role: 'royal' }} />)

    expect(await screen.findByRole('tab', { name: 'Мой календарь' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Команда' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Планирование' })).toBeVisible()
  })

  it('labels the drivers personal schedule template with the drivers role', async () => {
    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => []) })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, role: 'driver', username: 'driver' }} />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Шаблоны' }))
    expect(screen.getByRole('checkbox', { name: 'driver · Водитель' })).toBeChecked()
    expect(screen.queryByText('driver · Механик')).not.toBeInTheDocument()
  })

  it('requests only the visible Moscow month for the selected park and employee', async () => {
    const schedules = vi.fn(async () => [entry])
    render(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={7} user={mechanic} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Месяц' }))

    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()

    await waitFor(() => expect(schedules).toHaveBeenLastCalledWith(expect.objectContaining({
      parkId: 7,
      ownerUserId: 7,
      startAt: '2026-08-31T21:00:00.000Z',
      endAt: '2026-09-30T21:00:00.000Z',
      signal: expect.any(AbortSignal),
    })))
  })

  it('keeps data only for a matching scope and range and rejects stale responses', async () => {
    const sameScope = deferred<ScheduleEntry[]>()
    const changedScope = deferred<ScheduleEntry[]>()
    const firstEntry = { ...entry, id: 'first' }
    const staleEntry = { ...entry, id: 'stale', kind: 'sick' as const }
    const initialClient = client({ schedules: vi.fn(async () => [firstEntry]) })
    const refreshedSchedules = vi.fn()
      .mockReturnValueOnce(sameScope.promise)
      .mockReturnValueOnce(changedScope.promise)
    const refreshedClient = client({ schedules: refreshedSchedules })
    const view = render(<ScheduleWorkspace apiClient={initialClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, permissions: ['schedule.read'] }} />)
    expect(await screen.findByText('Моя смена')).toBeInTheDocument()

    view.rerender(<ScheduleWorkspace apiClient={refreshedClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, permissions: ['schedule.read'] }} />)
    expect(screen.getByText('Моя смена')).toBeInTheDocument()

    view.rerender(<ScheduleWorkspace apiClient={refreshedClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, permissions: ['schedule.read', 'schedule.write'] }} />)
    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()
    expect(refreshedSchedules.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal)
    expect(refreshedSchedules.mock.calls[0][0].signal.aborted).toBe(true)
    await act(async () => { changedScope.reject(new Error('denied')); await changedScope.promise.catch(() => undefined) })
    expect(await screen.findByText('Не удалось загрузить график')).toBeInTheDocument()
    await act(async () => { sameScope.resolve([staleEntry]); await sameScope.promise })
    expect(screen.queryByText('Моя болезнь')).not.toBeInTheDocument()
  })

  it('removes loaded shifts when a fresh request is denied', async () => {
    const view = render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()

    view.rerender(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => { throw new ApiError(403, 'forbidden') }) })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)

    expect(await screen.findByText('Не удалось загрузить график')).toBeVisible()
    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()
  })

  it('labels a stale schedule after a temporary failure and retries', async () => {
    const view = render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeVisible()
    const schedules = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, 'offline'))
      .mockResolvedValueOnce([{ ...entry, id: 'fresh', kind: 'vacation' }])
    view.rerender(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)

    const failure = await screen.findByText('Не удалось обновить график')
    expect(failure).toBeVisible()
    expect(screen.getByText('Моя смена')).toBeVisible()
    fireEvent.click(within(failure.closest('[role="alert"]') as HTMLElement).getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByText('Мой отпуск')).toBeVisible()
    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()
    expect(schedules).toHaveBeenCalledTimes(2)
  })

  it('keeps minimal schedule participants stable for an equivalent principal', async () => {
    const schedules = vi.fn(async () => [entry])
    const scheduleParticipants = vi.fn(async (): Promise<ScheduleParticipant[]> => [
      { id: 7, display_name: 'Анна', role: 'mechanic' },
      { id: 8, display_name: 'Олег', role: 'operator' },
    ])
    const apiClient = client({ schedules, scheduleParticipants })
    const royal = { ...mechanic, id: 99, role: 'royal', permissions: ['schedule.read'] }
    const view = render(<ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={royal} />)
    await screen.findByRole('rowheader', { name: 'Анна · Механик' })

    view.rerender(<ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...royal, permissions: ['schedule.read'] }} />)
    await waitFor(() => {
      expect(schedules).toHaveBeenCalledTimes(1)
      expect(scheduleParticipants).toHaveBeenCalledTimes(1)
      expect(scheduleParticipants).toHaveBeenCalledWith(1)
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Планирование' }))
    expect(screen.getByRole('checkbox', { name: 'Анна · Механик' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Олег · Оператор' })).toBeInTheDocument()
  })

  it('shows a participant loading failure in the team view and can retry', async () => {
    const scheduleParticipants = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce([{ id: 7, display_name: 'Анна', role: 'mechanic' }])
    render(<ScheduleWorkspace apiClient={client({ scheduleParticipants })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={{ ...mechanic, role: 'royal' }} />)

    expect(await screen.findByText('Не удалось загрузить участников графика')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку участников' }))
    expect(await screen.findByRole('rowheader', { name: 'Анна · Механик' })).toBeVisible()
    expect(scheduleParticipants).toHaveBeenCalledTimes(2)
  })

  it('waits for an explicit park before loading schedules or participants', () => {
    const schedules = vi.fn(async () => [entry])
    const scheduleParticipants = vi.fn(async () => [])
    render(<ScheduleWorkspace apiClient={client({ schedules, scheduleParticipants })} user={mechanic} />)

    expect(screen.getByText('Выберите парк')).toBeInTheDocument()
    expect(schedules).not.toHaveBeenCalled()
    expect(scheduleParticipants).not.toHaveBeenCalled()
  })

  it('closes an unsaved period editor when the selected park changes', async () => {
    const view = render(<ScheduleWorkspace apiClient={client()} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })

    view.rerender(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => []) })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={2} user={mechanic} />)

    expect(await screen.findByText('На выбранный день периодов нет')).toBeVisible()
    expect(screen.queryByLabelText('Начало')).not.toBeInTheDocument()
  })

  it('renders a compact phone list and lets an employee add their own period', async () => {
    const scheduleCreate = vi.fn(async () => ({ ...entry, id: 'created' }))
    render(<ScheduleWorkspace apiClient={client({ scheduleCreate })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Тип'), { target: { value: 'vacation' } })
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(scheduleCreate).toHaveBeenCalledWith(expect.objectContaining({ owner_user_id: undefined, kind: 'vacation' })))
  })

  it('keeps the schedule editor and explains a failed save', async () => {
    const scheduleCreate = vi.fn().mockRejectedValue(new Error('offline'))
    render(<ScheduleWorkspace apiClient={client({ scheduleCreate })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    expect(await screen.findByText('Не удалось сохранить период.')).toHaveAttribute('role', 'alert')
    expect(screen.getByLabelText('Начало')).toHaveValue('2026-09-22T09:00')
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeEnabled()
  })

  it('retries a lost single-period response with the same idempotency key', async () => {
    const scheduleCreate = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ...entry, id: 'created' })
    render(<ScheduleWorkspace apiClient={client({ scheduleCreate })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(scheduleCreate).toHaveBeenCalledTimes(2))
    const firstKey = scheduleCreate.mock.calls[0][0].idempotency_key
    expect(firstKey).toEqual(expect.any(String))
    expect(scheduleCreate.mock.calls[1][0].idempotency_key).toBe(firstKey)
  })

  it('retries an edit with its original revision and key after a lost response', async () => {
    const scheduleUpdate = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ...entry, kind: 'vacation', updated_at: '2026-09-21T12:00:00Z' })
    render(<ScheduleWorkspace apiClient={client({ scheduleUpdate })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }))
    fireEvent.change(screen.getByLabelText('Тип'), { target: { value: 'vacation' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(scheduleUpdate).toHaveBeenCalledTimes(2))
    const first = scheduleUpdate.mock.calls[0][1]
    expect(first.base_revision).toBe(entry.updated_at)
    expect(first.idempotency_key).toEqual(expect.any(String))
    expect(scheduleUpdate.mock.calls[1][1]).toEqual(first)
  })

  it('retries a delete with the visible entry revision and the same key', async () => {
    const scheduleDelete = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(undefined)
    render(<ScheduleWorkspace apiClient={client({ scheduleDelete })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Удалить' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Удалить' }))

    await waitFor(() => expect(scheduleDelete).toHaveBeenCalledTimes(2))
    const first = scheduleDelete.mock.calls[0][1]
    expect(first.base_revision).toBe(entry.updated_at)
    expect(first.idempotency_key).toEqual(expect.any(String))
    expect(scheduleDelete.mock.calls[1][1]).toEqual(first)
  })

  it('hides cached shifts and the editor when saving is denied', async () => {
    const scheduleCreate = vi.fn(async () => { throw new ApiError(403, 'forbidden') })
    render(<ScheduleWorkspace apiClient={client({ scheduleCreate })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    await screen.findByText('Моя смена')
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    expect(await screen.findByText('Не удалось загрузить график')).toBeVisible()
    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Начало')).not.toBeInTheDocument()
  })

  it('lets an employee open reusable self-only patterns', async () => {
    render(<ScheduleWorkspace apiClient={client()} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByRole('tab', { name: 'Шаблоны' })).toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: 'Шаблоны' }))
    expect(screen.getByRole('checkbox', { name: 'mech · Механик' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'mech · Механик' })).toBeDisabled()
  })

  it('keeps admin controls read-only', async () => {
    render(<ScheduleWorkspace apiClient={client()} selectedParkId={1} user={{ ...mechanic, role: 'admin' }} />)
    expect(await screen.findByRole('heading', { level: 1, name: 'График команды' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Добавить период' })).not.toBeInTheDocument()
  })

  it('edits and displays server timestamps in Moscow time', async () => {
    const utcEntry = { ...entry, start_at: '2026-09-21T06:00:00Z', end_at: '2026-09-21T18:00:00Z' }
    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => [utcEntry]) })} initialAnchor={new Date('2026-09-21T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText(/21\.09\.2026, 09:00/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }))
    expect(screen.getByLabelText('Начало')).toHaveValue('2026-09-21T09:00')
  })

  it('shows the bounded planner to royal users', async () => {
    const schedulePattern = vi.fn(async () => [{ ...entry, id: 'pattern-created' }])
    const scheduleCopy = vi.fn(async () => [])
    const scheduleParticipants = vi.fn(async (): Promise<ScheduleParticipant[]> => [
      { id: 7, display_name: 'Анна', role: 'mechanic' },
      { id: 8, display_name: 'Олег', role: 'operator' },
    ])
    render(<ScheduleWorkspace apiClient={client({ schedulePattern, scheduleCopy, scheduleParticipants })} selectedParkId={1} user={{ ...mechanic, role: 'royal' }} />)
    await screen.findByRole('heading', { level: 1, name: 'График команды' })
    fireEvent.click(screen.getByRole('tab', { name: 'Планирование' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Олег · Оператор' }))
    fireEvent.change(screen.getByLabelText('Тип графика'), { target: { value: '2/2' } })
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-09-22' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-09-30' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))
    await waitFor(() => expect(schedulePattern).toHaveBeenCalledWith(expect.objectContaining({ owner_user_ids: [7, 8], pattern: '2/2' })))
  })

})
