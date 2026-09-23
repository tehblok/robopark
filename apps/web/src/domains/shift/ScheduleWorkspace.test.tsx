import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../../api'
import { ScheduleWorkspace, type ScheduleApiClient } from './ScheduleWorkspace'

const park = { id: 1, name: 'Парк', tag: 'PARK', is_active: true }
const mechanic: User = { id: 7, username: 'mech', role: 'mechanic', access_status: 'approved', parks: [park] }
const entry = { id: 'one', owner_user_id: 7, park_id: 1, kind: 'shift' as const, start_at: '2026-09-21T09:00:00+03:00', end_at: '2026-09-21T21:00:00+03:00', source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [] }

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

  it('requests only the visible Moscow month for the selected park and employee', async () => {
    const schedules = vi.fn(async () => [entry])
    render(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={7} user={mechanic} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Месяц' }))

    expect(screen.queryByText('Моя смена')).not.toBeInTheDocument()

    await waitFor(() => expect(schedules).toHaveBeenLastCalledWith({
      parkId: 7,
      ownerUserId: 7,
      startAt: '2026-08-31T21:00:00.000Z',
      endAt: '2026-09-30T21:00:00.000Z',
    }))
  })

  it('keeps data only for a matching scope and range and rejects stale responses', async () => {
    const sameScope = deferred<typeof entry[]>()
    const changedScope = deferred<typeof entry[]>()
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
    await act(async () => { changedScope.reject(new Error('denied')); await changedScope.promise.catch(() => undefined) })
    expect(await screen.findByText('Не удалось загрузить график')).toBeInTheDocument()
    await act(async () => { sameScope.resolve([staleEntry]); await sameScope.promise })
    expect(screen.queryByText('Моя болезнь')).not.toBeInTheDocument()
  })

  it('keeps minimal schedule participants stable for an equivalent principal', async () => {
    const schedules = vi.fn(async () => [entry])
    const scheduleParticipants = vi.fn(async () => [
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

  it('waits for an explicit park before loading schedules or participants', () => {
    const schedules = vi.fn(async () => [entry])
    const scheduleParticipants = vi.fn(async () => [])
    render(<ScheduleWorkspace apiClient={client({ schedules, scheduleParticipants })} user={mechanic} />)

    expect(screen.getByText('Выберите парк')).toBeInTheDocument()
    expect(schedules).not.toHaveBeenCalled()
    expect(scheduleParticipants).not.toHaveBeenCalled()
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
    const scheduleParticipants = vi.fn(async () => [
      { id: 7, display_name: 'Анна', role: 'mechanic' },
      { id: 8, display_name: 'Олег', role: 'operator' },
    ])
    render(<ScheduleWorkspace apiClient={client({ schedulePattern, scheduleCopy, scheduleParticipants })} selectedParkId={1} user={{ ...mechanic, role: 'royal' }} />)
    await screen.findByRole('heading', { level: 1, name: 'График команды' })
    fireEvent.click(screen.getByRole('tab', { name: 'Планирование' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Олег · Оператор' }))
    fireEvent.change(screen.getByLabelText('Шаблон'), { target: { value: '2/2' } })
    fireEvent.change(screen.getByLabelText('Дата начала'), { target: { value: '2026-09-22' } })
    fireEvent.change(screen.getByLabelText('Дата окончания'), { target: { value: '2026-09-30' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))
    await waitFor(() => expect(schedulePattern).toHaveBeenCalledWith(expect.objectContaining({ owner_user_ids: [7, 8], pattern: '2/2' })))
  })
})
