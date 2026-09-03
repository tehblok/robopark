import type { RobotPhotoId } from './robotPhotos'
export const WHEEL_LABELS = { fl: 'Переднее левое колесо', ml: 'Среднее левое колесо', rl: 'Заднее левое колесо', fr: 'Переднее правое колесо', mr: 'Среднее правое колесо', rr: 'Заднее правое колесо' } as const
export type PhotoWheelSlot = keyof typeof WHEEL_LABELS
export type PhotoWheelHotspot = { slot: PhotoWheelSlot; x: number; y: number }
// Coordinates cover the entire original PNG, including its transparent margins.
const HOTSPOTS: Record<RobotPhotoId, PhotoWheelHotspot[]> = {
  top: [{ slot: 'fl', x: .202, y: .215 }, { slot: 'ml', x: .202, y: .465 }, { slot: 'rl', x: .202, y: .715 }, { slot: 'fr', x: .798, y: .215 }, { slot: 'mr', x: .798, y: .465 }, { slot: 'rr', x: .798, y: .715 }],
  left: [{ slot: 'fl', x: .18, y: .92 }, { slot: 'ml', x: .457, y: .92 }, { slot: 'rl', x: .743, y: .92 }],
  right: [{ slot: 'rr', x: .23, y: .92 }, { slot: 'mr', x: .52, y: .92 }, { slot: 'fr', x: .81, y: .92 }],
  front: [{ slot: 'fr', x: .18, y: .91 }, { slot: 'fl', x: .815, y: .91 }],
  rear: [{ slot: 'rl', x: .165, y: .92 }, { slot: 'rr', x: .835, y: .92 }],
  isometric: [],
}
export function photoWheelHotspots(view: RobotPhotoId): readonly PhotoWheelHotspot[] { return HOTSPOTS[view] }
export function splitWheelFaults(faults: string[]): { known: PhotoWheelSlot[]; unlocalized: boolean } {
  const known = [...new Set(faults.filter((slot): slot is PhotoWheelSlot => Object.hasOwn(WHEEL_LABELS, slot)))]
  return { known, unlocalized: faults.some(slot => !Object.hasOwn(WHEEL_LABELS, slot)) }
}
