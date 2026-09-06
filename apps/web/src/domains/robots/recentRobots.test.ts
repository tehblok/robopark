import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearRecentRobots,
  loadRecentRobots,
  RECENT_ROBOT_TTL_MS,
  rememberRobot,
} from './recentRobots'

describe('recent robots v2', () => {
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('isolates users, deduplicates by VIN and keeps newest first', () => {
    rememberRobot(7, { query: '447', vin: 'YASADR00000000447' }, 1_000)
    rememberRobot(8, { query: '888', vin: 'YASADR00000000888' }, 2_000)
    rememberRobot(7, { query: 'YASADR00000000447', vin: 'yasadr00000000447' }, 3_000)

    expect(loadRecentRobots(7, 3_001)).toEqual([
      { query: 'YASADR00000000447', vin: 'YASADR00000000447', openedAt: 3_000 },
    ])
    expect(loadRecentRobots(8, 3_001)).toHaveLength(1)
  })

  it('drops expired entries, caps at six and clears only the current user', () => {
    for (let index = 0; index < 7; index += 1) {
      rememberRobot(7, { query: String(index), vin: `YASADR${String(index).padStart(11, '0')}` }, index + 100)
    }
    localStorage.setItem('robopark.recentRobots', JSON.stringify(['legacy-447']))

    expect(loadRecentRobots(7, 200)).toHaveLength(6)
    expect(loadRecentRobots(7, RECENT_ROBOT_TTL_MS + 10_000)).toEqual([])
    clearRecentRobots(7)
    expect(localStorage.getItem('robopark.recentRobots')).toBe('["legacy-447"]')
  })

  it('expires robots at 48 hours and persists pruning without changing another user', () => {
    const entries = [
      { query: '447', vin: 'YASADR00000000447', openedAt: 1 },
      { query: '448', vin: 'YASADR00000000448', openedAt: 2 },
      { query: '449', vin: 'YASADR00000000449', openedAt: 0 },
    ]
    localStorage.setItem('robopark.recentRobots.v2.7', JSON.stringify(entries))
    localStorage.setItem('robopark.recentRobots.v2.8', JSON.stringify(entries))

    expect(loadRecentRobots(7, 172_800_001)).toEqual([
      { query: '448', vin: 'YASADR00000000448', openedAt: 2 },
    ])
    expect(JSON.parse(localStorage.getItem('robopark.recentRobots.v2.7')!)).toEqual([
      { query: '448', vin: 'YASADR00000000448', openedAt: 2 },
    ])
    expect(localStorage.getItem('robopark.recentRobots.v2.8')).toBe(JSON.stringify(entries))
  })

  it('removes robots older than 48 hours when remembering a newly opened robot', () => {
    rememberRobot(7, { query: '447', vin: 'YASADR00000000447' }, 1)
    rememberRobot(7, { query: '448', vin: 'YASADR00000000448' }, 172_800_002)

    expect(JSON.parse(localStorage.getItem('robopark.recentRobots.v2.7')!)).toEqual([
      { query: '448', vin: 'YASADR00000000448', openedAt: 172_800_002 },
    ])
  })

  it('discards malformed fields and impossible timestamps before presenting them', () => {
    localStorage.setItem('robopark.recentRobots.v2.7', JSON.stringify([
      { query: '447', vin: 'YASADR00000000447', openedAt: Number.NaN },
      { query: 447, vin: 'YASADR00000000447', openedAt: 100 },
      { query: '447', vin: 447, openedAt: 100 },
      { query: '447', vin: 'YASADR00000000447', openedAt: '100' },
      { query: '447', vin: 'YASADR00000000447', openedAt: 8.64e15 + 1 },
      { query: '447', vin: 'YASADR00000000447', openedAt: 100 },
    ]))

    expect(loadRecentRobots(7, 101)).toEqual([
      { query: '447', vin: 'YASADR00000000447', openedAt: 100 },
    ])
  })

  it('contains storage getter and method failures on reads, writes and clear', () => {
    const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(loadRecentRobots(7)).toEqual([])
    expect(() => rememberRobot(7, { query: '447', vin: 'YASADR00000000447' })).not.toThrow()
    expect(() => clearRecentRobots(7)).not.toThrow()
    getter.mockRestore()

    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(loadRecentRobots(7)).toEqual([])
    expect(() => rememberRobot(7, { query: '447', vin: 'YASADR00000000447' })).not.toThrow()
    expect(() => clearRecentRobots(7)).not.toThrow()
  })
})
