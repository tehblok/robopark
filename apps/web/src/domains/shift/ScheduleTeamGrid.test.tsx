import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ScheduleEntry, ScheduleParticipant } from '../../api'
import { visibleRange } from './scheduleCalendar'
import { ScheduleTeamGrid } from './ScheduleTeamGrid'

const employees: ScheduleParticipant[] = [
  { id: 7, display_name: 'Анна', role: 'mechanic' },
  { id: 8, display_name: 'Олег', role: 'operator' },
]
const items: ScheduleEntry[] = [{
  id: 'shift-one', owner_user_id: 7, park_id: 1, kind: 'shift',
  start_at: '2026-09-21T09:00:00+03:00', end_at: '2026-09-21T21:00:00+03:00',
  source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7,
  created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [],
}]

describe('ScheduleTeamGrid', () => {
  it('renders employee and date headers in the desktop matrix', () => {
    const days = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week').days
    render(<ScheduleTeamGrid days={days} employees={employees} items={items} selectedDate={days[0]} />)

    const matrix = screen.getByTestId('schedule-team-grid')
    expect(matrix).toHaveStyle({ '--day-count': '7' })
    expect(within(matrix).getByRole('rowheader', { name: 'Анна · Механик' })).toBeInTheDocument()
    expect(within(matrix).getByRole('columnheader', { name: /21 сентября/ })).toBeInTheDocument()
    expect(within(matrix).getByText('Смена')).toBeInTheDocument()
  })

  it('groups selected-day mobile cards by employee', () => {
    const days = visibleRange(new Date('2026-09-21T12:00:00+03:00'), 'week').days
    render(<ScheduleTeamGrid days={days} employees={employees} items={items} selectedDate={days[0]} />)

    const cards = screen.getByTestId('schedule-day-cards')
    expect(within(cards).getByRole('heading', { name: 'Анна' })).toBeInTheDocument()
    expect(within(cards).getByText('Смена')).toBeInTheDocument()
    expect(within(cards).queryByRole('heading', { name: 'Олег' })).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /22 сентября/ }).at(-1)!)

    expect(within(cards).getByText('На выбранный день периодов нет')).toBeInTheDocument()
  })
})
