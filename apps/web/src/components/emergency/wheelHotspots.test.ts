import { describe, expect, it } from 'vitest'
import { WHEEL_HOTSPOTS } from './wheelHotspots'

function pct(value: string): number {
  return Number.parseFloat(value)
}

describe('WHEEL_HOTSPOTS', () => {
  it('keeps all six slots over the robot drawing, not the PNG padding', () => {
    const slots = WHEEL_HOTSPOTS.map((item) => item.slot)
    expect(slots).toEqual(['fl', 'ml', 'rl', 'fr', 'mr', 'rr'])
    for (const hotspot of WHEEL_HOTSPOTS) {
      const top = pct(hotspot.top)
      const left = pct(hotspot.left)
      expect(top).toBeGreaterThan(38)
      expect(top).toBeLessThan(64)
      if (hotspot.slot.endsWith('l')) {
        expect(hotspot.side).toBe('left')
        expect(left).toBeGreaterThan(30)
        expect(left).toBeLessThan(40)
      } else {
        expect(hotspot.side).toBe('right')
        expect(left).toBeGreaterThan(60)
        expect(left).toBeLessThan(70)
      }
    }
  })
})
