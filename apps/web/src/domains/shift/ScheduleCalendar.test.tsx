import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ScheduleEntry } from '../../api'
import { visibleRange } from './scheduleCalendar'
import { ScheduleCalendar } from './PersonalScheduleCalendar'

const entries: ScheduleEntry[] = [
  {
    id: 'shift-one', owner_user_id: 7, park_id: 1, kind: 'shift',
    start_at: '2026-09-21T09:00:00+03:00', end_at: '2026-09-21T21:00:00+03:00',
    source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7,
    created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [],
  },
  {
    id: 'vacation-one', owner_user_id: 7, park_id: 1, kind: 'vacation',
    start_at: '2026-09-22T00:00:00+03:00', end_at: '2026-09-23T00:00:00+03:00',
    source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7,
    created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: ['overlap'],
  },
]

describe('ScheduleCalendar', () => {
  it('explains that more days can be reached by horizontal scrolling', () => {
    const range = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week')
    render(<ScheduleCalendar days={range.days} items={entries} ownerUserId={7} selectedDate={range.days[0]} view="week" onViewChange={vi.fn()} />)
    expect(screen.getByText('Листайте дни →')).toBeInTheDocument()
  })
  it('switches the selected day card without showing another day entries', () => {
    const range = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week')
    render(<ScheduleCalendar days={range.days} items={entries} ownerUserId={7} selectedDate={range.days[0]} view="week" onViewChange={vi.fn()} />)

    const cards = screen.getByTestId('schedule-day-cards')
    expect(within(cards).getByText('Моя смена')).toBeInTheDocument()
    expect(within(cards).queryByText('Отпуск')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /22 сентября/ }))

    expect(within(cards).getByText('Мой отпуск')).toBeInTheDocument()
    expect(within(cards).getByText('Пересечение')).toBeInTheDocument()
    expect(within(cards).queryByText('Моя смена')).not.toBeInTheDocument()
  })

  it('labels a pending period and keeps its edit actions unavailable', () => {
    const range = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week')
    render(<ScheduleCalendar days={range.days} items={entries} ownerUserId={7} pendingIds={new Set(['shift-one'])} selectedDate={range.days[0]} view="week" onDelete={vi.fn()} onEdit={vi.fn()} onViewChange={vi.fn()} />)

    expect(screen.getByText('Ожидает синхронизации')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Изменить' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Удалить' })).not.toBeInTheDocument()
  })

  it('exposes week and month controls through the shared callback', () => {
    const onViewChange = vi.fn()
    const range = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week')
    render(<ScheduleCalendar days={range.days} items={entries} ownerUserId={7} selectedDate={range.days[0]} view="week" onViewChange={onViewChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Месяц' }))

    expect(onViewChange).toHaveBeenCalledWith('month')
    expect(screen.getByRole('button', { name: 'Неделя' })).toHaveAttribute('aria-pressed', 'true')
  })
})
