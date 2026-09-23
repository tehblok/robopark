import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../../api'
import { ScheduleWorkspace, type ScheduleApiClient } from './ScheduleWorkspace'

const park = { id: 1, name: 'Парк', tag: 'PARK', is_active: true }
const mechanic: User = { id: 7, username: 'mech', role: 'mechanic', access_status: 'approved', parks: [park] }
const entry = { id: 'one', owner_user_id: 7, park_id: 1, kind: 'shift' as const, start_at: '2026-09-21T09:00:00+03:00', end_at: '2026-09-21T21:00:00+03:00', source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7, created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [] }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

function client(overrides: Partial<ScheduleApiClient> = {}): ScheduleApiClient {
  return { schedules: vi.fn(async () => [entry]), scheduleCreate: vi.fn(async () => entry), scheduleUpdate: vi.fn(async () => entry), scheduleDelete: vi.fn(async () => undefined), ...overrides }
}

describe('ScheduleWorkspace', () => {
  it('requests only the visible Moscow month for the selected park and employee', async () => {
    const schedules = vi.fn(async () => [entry])
    render(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={7} user={mechanic} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Месяц' }))

    await waitFor(() => expect(schedules).toHaveBeenLastCalledWith({
      parkId: 7,
      ownerUserId: 7,
      startAt: '2026-08-31T21:00:00.000Z',
      endAt: '2026-09-30T21:00:00.000Z',
    }))
  })

  it('keeps the last successful window visible and ignores a late park response', async () => {
    const parkTwo = deferred<typeof entry[]>()
    const parkThree = deferred<typeof entry[]>()
    const firstEntry = { ...entry, id: 'park-one' }
    const currentEntry = { ...entry, id: 'park-three', kind: 'vacation' as const }
    const staleEntry = { ...entry, id: 'park-two', kind: 'sick' as const }
    const schedules = vi.fn()
      .mockResolvedValueOnce([firstEntry])
      .mockReturnValueOnce(parkTwo.promise)
      .mockReturnValueOnce(parkThree.promise)
    const view = render(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={1} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeInTheDocument()

    view.rerender(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={2} user={mechanic} />)
    expect(screen.getByText('Моя смена')).toBeInTheDocument()
    view.rerender(<ScheduleWorkspace apiClient={client({ schedules })} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} selectedParkId={3} user={mechanic} />)

    await act(async () => { parkThree.resolve([currentEntry]); await parkThree.promise })
    expect(await screen.findByText('Моя отпуск')).toBeInTheDocument()
    await act(async () => { parkTwo.resolve([staleEntry]); await parkTwo.promise })
    expect(screen.getByText('Моя отпуск')).toBeInTheDocument()
    expect(screen.queryByText('Моя болезнь')).not.toBeInTheDocument()
  })

  it('keeps employee metadata stable and limits it to approved active park staff', async () => {
    const schedules = vi.fn(async () => [entry])
    const adminUsers = vi.fn(async () => [
      { id: 7, username: 'Анна', role: 'mechanic', is_active: true, access_status: 'approved', parks: [park] },
      { id: 8, username: 'Олег', role: 'operator', is_active: false, access_status: 'approved', parks: [park] },
      { id: 9, username: 'Ирина', role: 'mechanic', is_active: true, access_status: 'pending', parks: [park] },
      { id: 10, username: 'Водитель', role: 'driver', is_active: true, access_status: 'approved', parks: [park] },
      { id: 11, username: 'Другой парк', role: 'operator', is_active: true, access_status: 'approved', parks: [{ ...park, id: 2 }] },
    ])
    const apiClient = client({ schedules, adminUsers })
    const royal = { ...mechanic, id: 99, role: 'royal' }
    const view = render(<ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} user={royal} />)
    await screen.findByText('Смена · Анна')

    view.rerender(<ScheduleWorkspace apiClient={apiClient} initialAnchor={new Date('2026-09-15T12:00:00+03:00')} user={{ ...royal }} />)
    await waitFor(() => {
      expect(schedules).toHaveBeenCalledTimes(1)
      expect(adminUsers).toHaveBeenCalledTimes(1)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    expect(screen.getByRole('checkbox', { name: 'Анна · Механик' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /Олег|Ирина|Водитель|Другой парк/ })).not.toBeInTheDocument()
  })

  it('renders a compact phone list and lets an employee add their own period', async () => {
    const scheduleCreate = vi.fn(async () => entry)
    render(<ScheduleWorkspace apiClient={client({ scheduleCreate })} user={mechanic} />)
    expect(await screen.findByText('Моя смена')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.change(screen.getByLabelText('Тип'), { target: { value: 'vacation' } })
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(scheduleCreate).toHaveBeenCalledWith(expect.objectContaining({ owner_user_id: undefined, kind: 'vacation' })))
  })

  it('keeps admin controls read-only', async () => {
    render(<ScheduleWorkspace apiClient={client()} user={{ ...mechanic, role: 'admin' }} />)
    expect(await screen.findByRole('heading', { level: 1, name: 'График команды' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Добавить период' })).not.toBeInTheDocument()
  })

  it('edits and displays server timestamps in Moscow time', async () => {
    const utcEntry = { ...entry, start_at: '2026-09-21T06:00:00Z', end_at: '2026-09-21T18:00:00Z' }
    render(<ScheduleWorkspace apiClient={client({ schedules: vi.fn(async () => [utcEntry]) })} user={mechanic} />)
    expect(await screen.findByText(/21\.09\.2026, 09:00/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Изменить' }))
    expect(screen.getByLabelText('Начало')).toHaveValue('2026-09-21T09:00')
  })

  it('lets royal assign a bounded repeated schedule to several employees', async () => {
    const scheduleBulk = vi.fn(async () => [entry])
    const adminUsers = vi.fn(async () => [
      { id: 7, username: 'Анна', role: 'mechanic', is_active: true, access_status: 'approved', parks: [park] },
      { id: 8, username: 'Олег', role: 'operator', is_active: true, access_status: 'approved', parks: [park] },
    ])
    render(<ScheduleWorkspace apiClient={client({ scheduleBulk, adminUsers })} user={{ ...mechanic, role: 'royal' }} />)
    await screen.findByRole('heading', { level: 1, name: 'График команды' })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить период' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Олег · Оператор' }))
    fireEvent.change(screen.getByLabelText('Повторов'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Начало'), { target: { value: '2026-09-22T09:00' } })
    fireEvent.change(screen.getByLabelText('Конец'), { target: { value: '2026-09-22T21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(scheduleBulk).toHaveBeenCalledWith(expect.objectContaining({ owner_user_ids: [7, 8], repeat_count: 2 })))
  })
})
