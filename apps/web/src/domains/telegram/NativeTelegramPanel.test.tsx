import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api'
import { NativeTelegramPanel } from './NativeTelegramPanel'
import type { NativeTelegramClient, NativeTelegramState } from './nativeTelegramApi'

const state: NativeTelegramState = {
  health: { state: 'degraded', telegram_ok: true, scheduler_ok: false, updated_at: '2026-10-07T06:02:00Z', last_error: 'scheduler_delayed' },
  parks: [
    { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', chat_id: null, thread_id: null, revision: 2 },
    { id: 9, name: 'Южный', tag: 'south', timezone: 'Europe/Moscow', chat_id: -1001234567890, thread_id: 17, revision: 4 },
  ],
  jobs: [{
    id: '17fe4e2a-93e9-4a62-8d6a-806b9fcb0914', park_id: 7, kind: 'report', title: 'Утренний отчёт',
    enabled: true, schedule: 'daily', time: '09:00', weekdays: [0, 1, 2, 3, 4], start_hour: null,
    end_hour: null, text: null, url: null, tracker_tag: 'north', alternate: 'all', anchor_date: null, revision: 3,
  }],
  deliveries: [{
    id: 1, job_id: '17fe4e2a-93e9-4a62-8d6a-806b9fcb0914', park_id: 7,
    title: 'Утренний отчёт', state: 'failed', scheduled_at: '2026-10-07T06:00:00Z',
    finished_at: '2026-10-07T06:01:00Z', error_code: 'telegram_unavailable',
  }],
}

function client(): NativeTelegramClient {
  return {
    getAdmin: vi.fn().mockResolvedValue(structuredClone(state)),
    updatePark: vi.fn().mockImplementation(async (id, body) => ({ ...state.parks.find(park => park.id === id)!, ...body, revision: body.revision + 1 })),
    createJob: vi.fn().mockImplementation(async body => ({ ...body, id: crypto.randomUUID(), revision: 1 })),
    updateJob: vi.fn().mockImplementation(async (id, body) => ({ ...body, id, revision: body.revision + 1 })),
    deleteJob: vi.fn().mockResolvedValue(undefined),
    runJob: vi.fn().mockResolvedValue({ delivery: state.deliveries[0], created: true }),
    getAccount: vi.fn(), createLinkCode: vi.fn(), unlinkAccount: vi.fn(),
    getMigrationPreview: vi.fn(), applyMigration: vi.fn(),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(next => { resolve = next })
  return { promise, resolve }
}

afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
})

describe('NativeTelegramPanel', () => {
  it('creates alternating Zoom in an explicit timezone and keeps the anchor visible', async () => {
    const api = client()
    render(<NativeTelegramPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Тип задания'), { target: { value: 'zoom' } })
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Созвон группы B' } })
    fireEvent.change(screen.getByLabelText('Ссылка'), { target: { value: 'https://zoom.example/meeting' } })
    fireEvent.change(screen.getByLabelText('Чередование'), { target: { value: 'odd' } })
    fireEvent.change(screen.getByLabelText(/Опорная дата/), { target: { value: '2026-08-07' } })
    fireEvent.change(screen.getByLabelText(/Часовой пояс расписания/), { target: { value: 'Europe/Moscow' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать задание' }))
    await waitFor(() => expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ kind: 'zoom', alternate: 'odd', anchor_date: '2026-08-07', timezone: 'Europe/Moscow' })))
  })

  it('normalizes one-off schedules without recurring weekdays or alternation', async () => {
    const api = client()
    render(<NativeTelegramPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Разовая рассылка' } })
    fireEvent.change(screen.getByLabelText('Расписание'), { target: { value: 'once' } })
    fireEvent.change(screen.getByLabelText(/Дата и время отправки/), { target: { value: '2030-12-01T10:30' } })
    expect(screen.queryByLabelText('Чередование')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Создать задание' }))
    await waitFor(() => expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ schedule: 'once', weekdays: [], alternate: 'all', time: null, run_at: new Date('2030-12-01T10:30').toISOString() })))
  })

  it('requires confirmation and preserves the idempotency key after a failed send-now request', async () => {
    const api = client()
    vi.mocked(api.runJob).mockRejectedValueOnce(new Error('lost response')).mockResolvedValue({ delivery: state.deliveries[0], created: false })
    render(<NativeTelegramPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Отправить сейчас Утренний отчёт' }))
    expect(api.runJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(api.runJob).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Отправить' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(api.runJob).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.runJob).mock.calls[0]).toEqual(vi.mocked(api.runJob).mock.calls[1])
  })

  it('edits only assigned park settings and validates Telegram ids as safe integers', async () => {
    const api = client()
    render(<NativeTelegramPanel client={api} />)
    expect((await screen.findAllByText('Утренний отчёт'))[0]).toBeVisible()

    fireEvent.change(screen.getByLabelText('ID чата'), { target: { value: '9007199254740992' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить чат' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('безопасным целым')
    expect(api.updatePark).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('ID чата'), { target: { value: '-1007777777777' } })
    fireEvent.change(screen.getByLabelText('ID темы'), { target: { value: '42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить чат' }))
    await waitFor(() => expect(api.updatePark).toHaveBeenCalledWith(7, {
      chat_id: -1007777777777, thread_id: 42, revision: 2,
    }))

    fireEvent.change(screen.getByLabelText('Парк Telegram'), { target: { value: '9' } })
    expect(screen.queryByText('Утренний отчёт')).not.toBeInTheDocument()
    expect(screen.getByLabelText('ID чата')).toHaveValue('-1001234567890')
  })

  it('creates typed jobs, toggles them with revisions, deletes them, and shows delivery errors', async () => {
    const api = client()
    render(<NativeTelegramPanel client={api} />)
    expect(await screen.findByText('telegram_unavailable')).toBeVisible()
    expect(screen.getByText('Telegram отвечает')).toBeVisible()
    expect(screen.getByText('Планировщик не отвечает')).toBeVisible()
    expect(screen.getByText('Отчёт · В заданное время: 09:00 · Пн, Вт, Ср, Чт, Пт · время парка · включено')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Тип задания'), { target: { value: 'zoom' } })
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Общий Zoom' } })
    fireEvent.change(screen.getByLabelText('Ссылка'), { target: { value: 'https://zoom.example/room' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать задание' }))
    await waitFor(() => expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({
      park_id: 7, kind: 'zoom', title: 'Общий Zoom', url: 'https://zoom.example/room',
    })))

    fireEvent.click(screen.getByRole('button', { name: 'Отключить Утренний отчёт' }))
    await waitFor(() => expect(api.updateJob).toHaveBeenCalledWith(
      '17fe4e2a-93e9-4a62-8d6a-806b9fcb0914', expect.objectContaining({ enabled: false, revision: 3 }),
    ))
    fireEvent.click(screen.getByRole('button', { name: 'Удалить Утренний отчёт' }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить' }))
    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledWith('17fe4e2a-93e9-4a62-8d6a-806b9fcb0914', 4))
  })

  it('validates the effective Telegram body only when a draft is enabled', async () => {
    const api = client()
    render(<NativeTelegramPanel client={api} />)
    await screen.findByText('telegram_unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Подпись' } })
    fireEvent.change(screen.getByLabelText('Текст'), { target: { value: 'x'.repeat(500) + '{link}' } })
    fireEvent.change(screen.getByLabelText('Ссылка'), { target: { value: 'https://example.test/report' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Задание включено' }))
    fireEvent.click(screen.getByRole('button', { name: 'Создать задание' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('длиннее 500 символов')
    expect(api.createJob).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Задание включено' }))
    fireEvent.click(screen.getByRole('button', { name: 'Создать задание' }))
    await waitFor(() => expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ enabled: false })))
  })

  it('reports an optimistic conflict finitely and offers an authoritative reload', async () => {
    const api = client()
    api.updatePark = vi.fn().mockRejectedValue(new ApiError(409, 'stale_revision'))
    render(<NativeTelegramPanel client={api} />)
    await screen.findAllByText('Утренний отчёт')
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить чат' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('изменились в другой вкладке')
    fireEvent.click(screen.getByRole('button', { name: 'Загрузить свежие данные' }))
    await waitFor(() => expect(api.getAdmin).toHaveBeenCalledTimes(2))
  })

  it('keeps the new park and its unsaved chat fields when an older load resolves late', async () => {
    const api = client()
    const first = deferred<NativeTelegramState>()
    const second = deferred<NativeTelegramState>()
    api.getAdmin = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const view = render(<NativeTelegramPanel client={api} initialParkId={7} />)

    view.rerender(<NativeTelegramPanel client={api} initialParkId={9} />)
    await act(async () => second.resolve(structuredClone(state)))
    expect(await screen.findByLabelText('Парк Telegram')).toHaveValue('9')
    fireEvent.change(screen.getByLabelText('ID чата'), { target: { value: '-1009999999999' } })

    await act(async () => first.resolve(structuredClone(state)))
    expect(screen.getByLabelText('Парк Telegram')).toHaveValue('9')
    expect(screen.getByLabelText('ID чата')).toHaveValue('-1009999999999')
  })

  it('polls runtime fields while visible without overwriting configuration or drafts', async () => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    const api = client()
    const runtime = structuredClone(state)
    runtime.health = { state: 'offline', telegram_ok: true, scheduler_ok: true, updated_at: '2026-10-07T06:03:00Z', last_error: 'heartbeat_stale' }
    runtime.deliveries = [{ ...state.deliveries[0], id: 2, title: 'Новая доставка' }]
    runtime.parks[0].chat_id = -1001111111111
    runtime.jobs[0].title = 'Перезаписанное сервером задание'
    api.getAdmin = vi.fn().mockResolvedValueOnce(structuredClone(state)).mockResolvedValue(runtime)

    render(<NativeTelegramPanel client={api} />)
    await act(async () => {})
    fireEvent.change(screen.getByLabelText('ID чата'), { target: { value: '-1009999999999' } })
    fireEvent.click(screen.getByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Несохранённый черновик' } })

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(api.getAdmin).toHaveBeenCalledTimes(2)
    expect(screen.getByText(/Сервис бота офлайн/)).toBeVisible()
    expect(screen.getByText('Новая доставка')).toBeVisible()
    expect(screen.getByLabelText('ID чата')).toHaveValue('-1009999999999')
    expect(screen.getByLabelText('Название')).toHaveValue('Несохранённый черновик')
    expect(screen.getAllByText('Утренний отчёт')[0]).toBeVisible()
    expect(screen.queryByText('Перезаписанное сервером задание')).not.toBeInTheDocument()

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.getAdmin).toHaveBeenCalledTimes(2)
  })

  it('reloads migrated parks and jobs without overwriting unsaved editor fields', async () => {
    const api = client()
    const migration = {
      fingerprint: 'a'.repeat(64), already_applied: false,
      park_updates: [],
      jobs: [{
        source_ref: 'broadcasts:notice', source: 'broadcasts' as const, source_id: 'notice', park_id: 7,
        park_tag: 'north', title: 'Старое объявление', kind: 'text' as const, schedule: 'daily' as const,
        time: '10:00', weekdays: [0, 1, 2, 3, 4], start_hour: null, end_hour: null, text: 'Текст', url: null,
        tracker_tag: null, alternate: 'all' as const, anchor_date: null,
      }],
      conflicts: [], counts: { park_updates: 0, jobs: 1, conflicts: 0, skipped: 0 },
    }
    const migrated = structuredClone(state)
    migrated.jobs.push({
      id: 'd30fe9b1-5b7d-4987-a956-7cb09c4fc0e7', revision: 1, park_id: 7, kind: 'text', title: 'Старое объявление',
      enabled: false, schedule: 'daily', time: '10:00', weekdays: [0, 1, 2, 3, 4], start_hour: null,
      end_hour: null, text: 'Текст', url: null, tracker_tag: null, alternate: 'all', anchor_date: null,
    })
    api.getAdmin = vi.fn().mockResolvedValueOnce(structuredClone(state)).mockResolvedValueOnce(migrated)
    api.getMigrationPreview = vi.fn().mockResolvedValue(migration)
    api.applyMigration = vi.fn().mockResolvedValue({ ...migration, applied: true as const, applied_at: '2026-10-07T12:00:00Z' })

    render(<NativeTelegramPanel client={api} showMigration />)
    await screen.findByRole('button', { name: 'Перенести 1 выключенных заданий' })
    fireEvent.change(screen.getByLabelText('ID чата'), { target: { value: '-1009999999999' } })
    fireEvent.click(screen.getByRole('button', { name: 'Новое задание' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Несохранённый черновик' } })
    fireEvent.click(screen.getByRole('button', { name: 'Перенести 1 выключенных заданий' }))

    expect(await screen.findByText('Старое объявление')).toBeVisible()
    expect(screen.getByLabelText('ID чата')).toHaveValue('-1009999999999')
    expect(screen.getByLabelText('Название')).toHaveValue('Несохранённый черновик')
  })
})
