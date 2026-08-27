import { afterEach, describe, expect, it } from 'vitest'
import { resourceStore } from './resource'

describe('resourceStore', () => {
  afterEach(() => {
    resourceStore.clearAll()
  })

  it('returns memory values immediately', () => {
    resourceStore.set('now-report:all', { totals: { blocker: 3 } }, false)
    expect(resourceStore.get('now-report:all')).toEqual({ totals: { blocker: 3 } })
  })

  it('invalidates a single key without touching neighbors', () => {
    resourceStore.set('tracker:issue:A', { key: 'A' }, false)
    resourceStore.set('tracker:issue:B', { key: 'B' }, false)
    resourceStore.invalidate('tracker:issue:A')
    expect(resourceStore.get('tracker:issue:A')).toBeUndefined()
    expect(resourceStore.get('tracker:issue:B')).toEqual({ key: 'B' })
  })

  it('invalidates by prefix', () => {
    resourceStore.set('emergency:snapshot:VIN1', { vin: 'VIN1' }, false)
    resourceStore.set('emergency:section:VIN1:map', { id: 'map' }, false)
    resourceStore.set('now-report:all', { ok: true }, false)
    resourceStore.invalidate('emergency:', { prefix: true })
    expect(resourceStore.get('emergency:snapshot:VIN1')).toBeUndefined()
    expect(resourceStore.get('emergency:section:VIN1:map')).toBeUndefined()
    expect(resourceStore.get('now-report:all')).toEqual({ ok: true })
  })

  it('clearAll drops every entry', () => {
    resourceStore.set('operator:parks', [{ id: 1 }], false)
    resourceStore.clearAll()
    expect(resourceStore.get('operator:parks')).toBeUndefined()
  })

  it('ignores persisted entries older than the soft TTL', () => {
    const key = 'now-report:stale'
    window.localStorage.setItem(
      `robopark:res:${key}`,
      JSON.stringify({ v: 1, updatedAt: 1, data: { totals: { blocker: 99 } } }),
    )
    expect(resourceStore.get(key)).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${key}`)).toBeNull()
  })
})
