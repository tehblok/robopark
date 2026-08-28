export const WHEEL_HOTSPOTS = [
  { slot: 'fl', side: 'left', top: '43%', left: '34%' },
  { slot: 'ml', side: 'left', top: '51%', left: '34%' },
  { slot: 'rl', side: 'left', top: '58%', left: '34%' },
  { slot: 'fr', side: 'right', top: '43%', left: '66%' },
  { slot: 'mr', side: 'right', top: '51%', left: '66%' },
  { slot: 'rr', side: 'right', top: '58%', left: '66%' },
] as const

export type WheelSlot = (typeof WHEEL_HOTSPOTS)[number]['slot']
