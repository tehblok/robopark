import { afterEach, describe, expect, it } from 'vitest'
import { clearRecentRobots, loadRecentRobots, pushRecentRobot } from './recentRobots'

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

  it('clearRecentRobots wipes storage', () => {
    pushRecentRobot('447')
    clearRecentRobots()
    expect(loadRecentRobots()).toEqual([])
    expect(window.localStorage.getItem('robopark.recentRobots')).toBeNull()
  })
})
