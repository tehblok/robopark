import { expect, it } from 'vitest'
import { photoWheelHotspots, splitWheelFaults } from './robotPhotoHotspots'
it('maps only visible wheels, with vehicle-left reversed in front', () => {
  expect(photoWheelHotspots('top').map(h => h.slot)).toEqual(['fl', 'ml', 'rl', 'fr', 'mr', 'rr'])
  expect(photoWheelHotspots('left').map(h => h.slot)).toEqual(['fl', 'ml', 'rl'])
  expect(photoWheelHotspots('right').map(h => h.slot)).toEqual(['rr', 'mr', 'fr'])
  expect(photoWheelHotspots('front').map(h => h.slot)).toEqual(['fr', 'fl'])
  expect(photoWheelHotspots('rear').map(h => h.slot)).toEqual(['rl', 'rr'])
  expect(photoWheelHotspots('isometric')).toEqual([])
  expect(photoWheelHotspots('top')[0]).toMatchObject({ x: .202, y: .215 })
  for (const view of ['top', 'left', 'right', 'front', 'rear', 'isometric'] as const) for (const h of photoWheelHotspots(view)) {
    expect(Number.isFinite(h.x) && h.x > 0 && h.x < 1 && Number.isFinite(h.y) && h.y > 0 && h.y < 1).toBe(true)
  }
})
it('does not anatomically localize opaque body or unknown wheel codes', () => {
  expect(splitWheelFaults(['body', 'unknown', 'fl', 'fl'])).toEqual({ known: ['fl'], unlocalized: true })
  expect(splitWheelFaults([])).toEqual({ known: [], unlocalized: false })
})
