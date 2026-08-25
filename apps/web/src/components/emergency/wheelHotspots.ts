export const WHEEL_HOTSPOTS = [
  { slot: 'fl', top: '22%', left: '7%' },
  { slot: 'ml', top: '48%', left: '7%' },
  { slot: 'rl', top: '74%', left: '7%' },
  { slot: 'fr', top: '22%', left: '86%' },
  { slot: 'mr', top: '48%', left: '86%' },
  { slot: 'rr', top: '74%', left: '86%' },
] as const

export type WheelSlot = (typeof WHEEL_HOTSPOTS)[number]['slot']
