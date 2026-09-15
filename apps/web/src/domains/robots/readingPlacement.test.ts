import { expect, it } from 'vitest'
import { placeLabels, type LabelBox } from './readingPlacement'

const frame = { width: 400, height: 300 }
const robot = { left: 120, top: 60, right: 280, bottom: 240, width: 160, height: 180, x: 120, y: 60, toJSON: () => ({}) } as DOMRectReadOnly
const overlaps = (a: { left: number; top: number; width: number; height: number }, b: { left: number; top: number; width: number; height: number }) =>
  a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top

it('places labels deterministically outside the robot and honors a valid manual direction', () => {
  const items: LabelBox[] = [{ id: 'manual', x: 100, y: 100, width: 80, height: 30, direction: 'left', priority: 'selected' }]
  const first = placeLabels(items, frame, robot)
  expect(first).toEqual(placeLabels(items, frame, robot))
  expect(first[0]).toMatchObject({ id: 'manual', collapsed: false })
  expect(first[0].left + first[0].width).toBeLessThanOrEqual(items[0].x)
  expect(overlaps(first[0], robot)).toBe(false)
})
it('places error, critical, selected and warning labels in priority order regardless of input order', () => {
  const items: LabelBox[] = [
    { id: 'warning', x: 200, y: 150, width: 110, height: 38, direction: 'auto', priority: 'warning' },
    { id: 'selected', x: 200, y: 150, width: 110, height: 38, direction: 'auto', priority: 'selected' },
    { id: 'error', x: 200, y: 150, width: 110, height: 38, direction: 'auto', priority: 'error' },
    { id: 'critical', x: 200, y: 150, width: 110, height: 38, direction: 'auto', priority: 'critical' },
  ]
  const placed = placeLabels(items, { width: 260, height: 170 }, { ...robot, left: 80, top: 40, right: 180, bottom: 130, width: 100, height: 90 } as DOMRectReadOnly)
  expect(placed.map(item => item.id)).toEqual(['error', 'critical', 'selected', 'warning'])
  expect(placed.find(item => item.id === 'error')?.collapsed).toBe(false)
  expect(placed.at(-1)?.collapsed).toBe(true)
})
it('never overlaps placed labels and collapses unplaceable labels into the side list', () => {
  const items: LabelBox[] = [
    { id: 'a', x: 100, y: 40, width: 90, height: 30, direction: 'top', priority: 'error' },
    { id: 'b', x: 100, y: 40, width: 90, height: 30, direction: 'top', priority: 'critical' },
  ]
  const placed = placeLabels(items, { width: 200, height: 100 }, { ...robot, left: 60, top: 25, right: 140, bottom: 90, width: 80, height: 65 } as DOMRectReadOnly)
  const visible = placed.filter(item => !item.collapsed)
  for (let index = 0; index < visible.length; index += 1) {
    expect(overlaps(visible[index], robot)).toBe(false)
    for (let other = index + 1; other < visible.length; other += 1) expect(overlaps(visible[index], visible[other])).toBe(false)
  }
  expect(placed.some(item => item.collapsed)).toBe(true)
})
