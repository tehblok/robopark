import { describe, expect, it } from 'vitest'
import { canSearchRobotTickets, searchPathForRole } from './robotSearch'

describe('robotSearch', () => {
  it('sends mechanics and operators to their park-scoped APIs', () => {
    expect(searchPathForRole('mechanic')).toBe('mechanic')
    expect(searchPathForRole('operator')).toBe('operator')
    expect(canSearchRobotTickets('mechanic', [])).toBe(true)
    expect(canSearchRobotTickets('operator', [])).toBe(true)
  })

  it('lets staff search via Tracker when they have read access', () => {
    expect(searchPathForRole('royal')).toBe('tracker')
    expect(canSearchRobotTickets('royal', ['tracker.read'])).toBe(true)
    expect(canSearchRobotTickets('driver', [])).toBe(false)
  })
})
