import { afterEach, describe, expect, it } from 'vitest'
import { loadRecentRobots, pushRecentRobot } from './recentRobots'

describe('recentRobots', () => {
  afterEach(() => {
    window.localStorage.clear()
  })

  it('stores unique queries newest first', () => {
    pushRecentRobot('447')
    pushRecentRobot('448')
    pushRecentRobot('447')
    expect(loadRecentRobots()).toEqual(['447', '448'])
  })
})
