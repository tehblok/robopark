export type LabelPriority = 'error' | 'critical' | 'selected' | 'warning'
export type LabelDirection = 'auto' | 'left' | 'right' | 'top' | 'bottom'
export type LabelBox = {
  id: string
  x: number
  y: number
  width: number
  height: number
  direction: LabelDirection
  priority?: LabelPriority
}
export type PlacedLabel = LabelBox & { left: number; top: number; collapsed: boolean }

const PRIORITY: Record<LabelPriority, number> = { error: 0, critical: 1, selected: 2, warning: 3 }
const GAP = 12

function intersects(a: { left: number; top: number; width: number; height: number }, b: { left: number; top: number; width: number; height: number }) {
  return a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top
}

function candidate(item: LabelBox, direction: Exclude<LabelDirection, 'auto'>) {
  if (direction === 'left') return { left: item.x - item.width - GAP, top: item.y - item.height / 2 }
  if (direction === 'right') return { left: item.x + GAP, top: item.y - item.height / 2 }
  if (direction === 'top') return { left: item.x - item.width / 2, top: item.y - item.height - GAP }
  return { left: item.x - item.width / 2, top: item.y + GAP }
}

export function placeLabels(items: LabelBox[], frame: { width: number; height: number }, robotBox: DOMRectReadOnly): PlacedLabel[] {
  const placed: PlacedLabel[] = []
  const robot = { left: robotBox.left, top: robotBox.top, width: robotBox.width, height: robotBox.height }
  const ordered = items.map((item, index) => ({ item, index })).sort((a, b) =>
    PRIORITY[a.item.priority ?? 'warning'] - PRIORITY[b.item.priority ?? 'warning'] || a.index - b.index)
  for (const { item } of ordered) {
    const directions: Exclude<LabelDirection, 'auto'>[] = item.direction === 'auto'
      ? ['left', 'right', 'top', 'bottom']
      : [item.direction]
    const position = directions.map(direction => candidate(item, direction)).find(value => {
      const box = { ...value, width: item.width, height: item.height }
      return value.left >= 0 && value.top >= 0
        && value.left + item.width <= frame.width && value.top + item.height <= frame.height
        && !intersects(box, robot) && placed.every(label => label.collapsed || !intersects(box, label))
    })
    placed.push({ ...item, left: position?.left ?? 0, top: position?.top ?? 0, collapsed: !position })
  }
  return placed
}
