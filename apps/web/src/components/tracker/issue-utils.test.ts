import { describe, expect, it } from 'vitest'
import {
  formatAge,
  initials,
  parseTrackerDate,
  priorityTone,
  statusTone,
} from './issue-utils'

describe('parseTrackerDate', () => {
  it('parses Tracker +0000 offset (Safari-safe)', () => {
    const date = parseTrackerDate('2024-06-15T14:30:00+0000')
    expect(date).not.toBeNull()
    expect(date!.toISOString()).toBe('2024-06-15T14:30:00.000Z')
  })

  it('parses Z suffix', () => {
    const date = parseTrackerDate('2024-06-15T14:30:00Z')
    expect(date).not.toBeNull()
    expect(date!.toISOString()).toBe('2024-06-15T14:30:00.000Z')
  })

  it('parses +00:00 offset', () => {
    const date = parseTrackerDate('2024-06-15T14:30:00+00:00')
    expect(date).not.toBeNull()
  })

  it('returns null for empty or invalid input', () => {
    expect(parseTrackerDate(null)).toBeNull()
    expect(parseTrackerDate('')).toBeNull()
    expect(parseTrackerDate('not-a-date')).toBeNull()
  })
})

describe('formatAge', () => {
  it('formats sub-hour age', () => {
    expect(formatAge('0.5')).toBe('0.5 ч')
  })

  it('formats hours', () => {
    expect(formatAge('3')).toBe('3.0 ч')
  })

  it('formats days', () => {
    expect(formatAge('48')).toBe('48.0 ч')
  })

  it('returns empty for missing or invalid values', () => {
    expect(formatAge(null)).toBe('')
    expect(formatAge('-1')).toBe('')
  })
})

describe('initials', () => {
  it('uses first letters of two-word display names', () => {
    expect(initials({ display: 'Ivan Petrov', login: 'ivan' })).toBe('IP')
  })

  it('uses first two characters for single-word names', () => {
    expect(initials({ display: 'mechanic', login: 'mechanic' })).toBe('ME')
  })

  it('returns em dash when assignee is missing', () => {
    expect(initials(null)).toBe('—')
  })
})

describe('priorityTone', () => {
  it('maps blocker priorities', () => {
    expect(priorityTone('blocker')).toBe('blocker')
    expect(priorityTone('critical')).toBe('blocker')
  })

  it('maps major to high', () => {
    expect(priorityTone('major')).toBe('high')
  })

  it('defaults to normal', () => {
    expect(priorityTone(null)).toBe('normal')
  })
})

describe('statusTone', () => {
  it('detects closed statuses', () => {
    expect(statusTone({ status: 'Closed', status_key: 'closed' })).toBe('done')
  })

  it('detects waiting statuses', () => {
    expect(statusTone({ status: 'Waiting for parts', status_key: null })).toBe('waiting')
  })

  it('detects in-progress statuses', () => {
    expect(statusTone({ status: 'In progress', status_key: 'inProgress' })).toBe('progress')
  })

  it('defaults to open', () => {
    expect(statusTone({ status: 'Open', status_key: 'open' })).toBe('open')
  })
})
