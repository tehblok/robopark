import { describe, expect, it } from 'vitest'
import { formatDurationHours, moscowWorkingHoursBetween } from './timeFormat'

describe('duration presentation', () => {
  it('uses one decimal without changing the input precision', () => {
    expect(formatDurationHours(4.94)).toBe('4.9 ч')
    expect(formatDurationHours(0)).toBe('0.0 ч')
    expect(formatDurationHours(5)).toBe('5.0 ч')
  })

  it('counts only working time across a night', () => {
    expect(moscowWorkingHoursBetween(Date.parse('2026-09-18T17:00:00Z'), Date.parse('2026-09-19T10:00:00Z'))).toBe(5)
    expect(moscowWorkingHoursBetween(Date.parse('2026-09-18T19:00:00Z'), Date.parse('2026-09-19T06:00:00Z'))).toBe(0)
  })
})
