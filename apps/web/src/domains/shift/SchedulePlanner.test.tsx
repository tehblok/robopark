import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ScheduleEntry, ScheduleParticipant } from '../../api'
import { SchedulePlanner } from './SchedulePlanner'

const employees: ScheduleParticipant[] = [
  { id: 11, display_name: 'Анна', role: 'mechanic' },
  { id: 12, display_name: 'Олег', role: 'operator' },
]

const created: ScheduleEntry = {
  id: 'planned', owner_user_id: 11, park_id: 7, kind: 'shift',
  start_at: '2026-09-03T09:00:00+03:00', end_at: '2026-09-03T21:00:00+03:00',
  source: 'royal', series_id: 'series', created_by_user_id: 1, updated_by_user_id: 1,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', warnings: [],
}

describe('SchedulePlanner', () => {
  it('queues a pattern instead of sending it directly while offline', async () => {
    const apiClient = { schedulePattern: vi.fn(async () => [created]), scheduleCopy: vi.fn(async () => []) }
    const onQueue = vi.fn(async () => undefined)
    render(<SchedulePlanner apiClient={apiClient} employees={employees} onQueue={onQueue} parkId={7} queueReady />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-09-03' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-09-03' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))
    await waitFor(() => expect(onQueue).toHaveBeenCalledWith('schedule_pattern', expect.objectContaining({ park_id: 7, owner_user_ids: [11] }), expect.any(String)))
    expect(apiClient.schedulePattern).not.toHaveBeenCalled()
  })
  it('submits selected employees and a 4/4 pattern', async () => {
    const apiClient = {
      schedulePattern: vi.fn(async () => [created]),
      scheduleCopy: vi.fn(async () => []),
    }
    render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Олег · Оператор' }))
    fireEvent.change(screen.getByLabelText('Тип графика'), { target: { value: '4/4' } })
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-09-03' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-09-14' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))

    await waitFor(() => expect(apiClient.schedulePattern).toHaveBeenCalledWith(expect.objectContaining({
      park_id: 7,
      pattern: '4/4',
      owner_user_ids: [11, 12],
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    })))
  })

  it('uses the first work day and period end for a 3/3 batch', async () => {
    const apiClient = {
      schedulePattern: vi.fn(async () => [created]),
      scheduleCopy: vi.fn(async () => []),
    }
    render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.change(screen.getByLabelText('Тип графика'), { target: { value: '3/3' } })
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-10-31' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))

    await waitFor(() => expect(apiClient.schedulePattern).toHaveBeenCalledWith(expect.objectContaining({
      pattern: '3/3',
      start_date: '2026-10-01',
      end_date: '2026-10-31',
    })))
  })

  it('copies an existing period for the selected employees', async () => {
    const apiClient = {
      schedulePattern: vi.fn(async () => []),
      scheduleCopy: vi.fn(async () => [created]),
    }
    render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.change(screen.getByLabelText('Копировать с'), { target: { value: '2026-09-01T00:00' } })
    fireEvent.change(screen.getByLabelText('Копировать по'), { target: { value: '2026-09-08T00:00' } })
    fireEvent.change(screen.getByLabelText('Начало копии'), { target: { value: '2026-10-01T00:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Копировать период' }))

    await waitFor(() => expect(apiClient.scheduleCopy).toHaveBeenCalledWith(expect.objectContaining({
      park_id: 7,
      owner_user_ids: [11],
      source_start: new Date('2026-09-01T00:00').toISOString(),
      source_end: new Date('2026-09-08T00:00').toISOString(),
      target_start: new Date('2026-10-01T00:00').toISOString(),
    })))
  })

  it('reuses one idempotency key when a failed submission is retried', async () => {
    const schedulePattern = vi.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce([created])
    const apiClient = { schedulePattern, scheduleCopy: vi.fn(async () => []) }
    render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.change(screen.getByLabelText('Первый день смены'), { target: { value: '2026-09-03' } })
    fireEvent.change(screen.getByLabelText('Создавать до'), { target: { value: '2026-09-14' } })
    fireEvent.change(screen.getByLabelText('Время начала'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('Время окончания'), { target: { value: '21:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Создать смены' }))

    await waitFor(() => expect(schedulePattern).toHaveBeenCalledTimes(2))
    const firstKey = schedulePattern.mock.calls[0][0].idempotency_key
    expect(firstKey).toEqual(expect.any(String))
    expect(schedulePattern.mock.calls[1][0].idempotency_key).toBe(firstKey)
  })

  it('reuses one copy idempotency key when a failed copy is retried', async () => {
    const scheduleCopy = vi.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce([created])
    const apiClient = { schedulePattern: vi.fn(async () => []), scheduleCopy }
    render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    fireEvent.change(screen.getByLabelText('Копировать с'), { target: { value: '2026-09-01T00:00' } })
    fireEvent.change(screen.getByLabelText('Копировать по'), { target: { value: '2026-09-08T00:00' } })
    fireEvent.change(screen.getByLabelText('Начало копии'), { target: { value: '2026-10-01T00:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Копировать период' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Копировать период' }))

    await waitFor(() => expect(scheduleCopy).toHaveBeenCalledTimes(2))
    const firstKey = scheduleCopy.mock.calls[0][0].idempotency_key
    expect(firstKey).toEqual(expect.any(String))
    expect(scheduleCopy.mock.calls[1][0].idempotency_key).toBe(firstKey)
  })

  it('drops a selected employee who leaves the visible park team before submission', async () => {
    const apiClient = { schedulePattern: vi.fn(async () => []), scheduleCopy: vi.fn(async () => []) }
    const view = render(<SchedulePlanner apiClient={apiClient} employees={employees} parkId={7} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Анна · Механик' }))
    expect(screen.getByRole('button', { name: 'Создать смены' })).toBeEnabled()

    view.rerender(<SchedulePlanner apiClient={apiClient} employees={[employees[1]]} parkId={7} />)
    expect(screen.getByRole('checkbox', { name: 'Олег · Оператор' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Создать смены' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Копировать период' })).toBeDisabled()
  })
})
