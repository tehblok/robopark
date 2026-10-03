import { describe, expect, it } from 'vitest'
import { formatDurationHours, formatParkDateTime, workingHoursBetween } from './timeFormat'

describe('duration presentation', () => {
  it('formats an SLA deadline in the park timezone and rejects unknown input', () => {
    expect(formatParkDateTime('2026-09-03T13:00:00Z', 'Europe/Moscow')).toMatch(/03\.09.*16:00/)
    expect(formatParkDateTime(null, 'Europe/Moscow')).toBeNull()
    expect(formatParkDateTime('not-a-date', 'Europe/Moscow')).toBeNull()
    expect(formatParkDateTime('2026-09-03T13:00:00Z', 'bad-timezone')).toBeNull()
  })

  it('uses one decimal without changing the input precision', () => {
    expect(formatDurationHours(4.94)).toBe('4.9 ч')
    expect(formatDurationHours(0)).toBe('0.0 ч')
    expect(formatDurationHours(5)).toBe('5.0 ч')
  })

  it('counts only working time across a night', () => {
    expect(workingHoursBetween(Date.parse('2026-09-18T17:00:00Z'), Date.parse('2026-09-19T10:00:00Z'), 'Europe/Moscow')).toBe(5)
    expect(workingHoursBetween(Date.parse('2026-09-18T19:00:00Z'), Date.parse('2026-09-19T06:00:00Z'), 'Europe/Moscow')).toBe(0)
  })

  it('uses each park day across a daylight-saving transition', () => {
    expect(workingHoursBetween(
      Date.parse('2026-03-28T19:00:00Z'),
      Date.parse('2026-03-29T08:00:00Z'),
      'Europe/Berlin',
    )).toBe(2)
  })
})
