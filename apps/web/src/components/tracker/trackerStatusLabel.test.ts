import { describe, expect, it } from 'vitest'
import { trackerStatusLabel } from './trackerStatusLabel'

describe('trackerStatusLabel', () => {
  it('translates technical workflow keys and English labels', () => {
    expect(trackerStatusLabel('open')).toBe('Открыта')
    expect(trackerStatusLabel('Open', 'queued')).toBe('В очереди')
    expect(trackerStatusLabel('in-progress')).toBe('В работе')
  })

  it('preserves an already readable or unknown Tracker status', () => {
    expect(trackerStatusLabel('Ожидает запчасть')).toBe('Ожидает запчасть')
  })
})
