import { describe, expect, it } from 'vitest'
import type { ScheduleEntry } from '../../api'
import { formatDayKey, projectSchedule, visibleRange } from './scheduleCalendar'

const entry = (overrides: Partial<ScheduleEntry> = {}): ScheduleEntry => ({
  id: 'entry',
  owner_user_id: 7,
  park_id: 1,
  kind: 'shift',
  start_at: '2026-09-30T21:30:00+03:00',
  end_at: '2026-10-01T08:00:00+03:00',
  source: 'self',
  series_id: null,
  created_by_user_id: 7,
  updated_by_user_id: 7,
  created_at: '2026-09-30T12:00:00Z',
  updated_at: '2026-09-30T12:00:00Z',
  warnings: [],
  ...overrides,
})

describe('scheduleCalendar', () => {
  it('projects a year-crossing Moscow week', () => {
    const range = visibleRange(new Date('2026-12-31T12:00:00+03:00'), 'week')

    expect(range.days.map(formatDayKey)).toEqual([
      '2026-12-28',
      '2026-12-29',
      '2026-12-30',
      '2026-12-31',
      '2027-01-01',
      '2027-01-02',
      '2027-01-03',
    ])
    expect(formatDayKey(range.start)).toBe('2026-12-28')
    expect(formatDayKey(range.end)).toBe('2027-01-04')
  })

  it('uses exact Moscow month boundaries', () => {
    const range = visibleRange(new Date('2027-01-15T22:00:00Z'), 'month')

    expect(range.days).toHaveLength(31)
    expect(range.days.map(formatDayKey)).toEqual(expect.arrayContaining(['2027-01-01', '2027-01-31']))
    expect(formatDayKey(range.start)).toBe('2027-01-01')
    expect(formatDayKey(range.end)).toBe('2027-02-01')
    expect(formatDayKey(new Date('2026-12-31T21:30:00Z'))).toBe('2027-01-01')
  })

  it('projects overnight entries onto both intersected days with half-open ends', () => {
    const days = visibleRange(new Date('2026-09-30T12:00:00+03:00'), 'week').days
    const overnight = entry()
    const endingAtMidnight = entry({
      id: 'ending-at-midnight',
      start_at: '2026-09-30T20:00:00+03:00',
      end_at: '2026-10-01T00:00:00+03:00',
    })

    const projected = projectSchedule([overnight, endingAtMidnight], days).get(7)

    expect(projected?.get('2026-09-30')).toEqual([overnight, endingAtMidnight])
    expect(projected?.get('2026-10-01')).toEqual([overnight])
  })
})
