import { describe, expect, it } from 'vitest'
import { createInterfaceModeStore, type InterfaceStorage } from './interfaceModeStore'

function setup() {
  const values = new Map<string, string>()
  const storage: InterfaceStorage = {
    getItem: key => values.get(key) ?? null,
    removeItem: key => { values.delete(key) },
  }
  return { values, store: createInterfaceModeStore(() => storage) }
}

describe('classic interface lifecycle', () => {
  it('removes a stored legacy preference without retaining interface selection state', () => {
    const { store, values } = setup()
    values.set('robopark:interface:v1:810', ['task', 'first'].join('-'))

    store.setAccount(810)

    expect(store.getSnapshot()).toEqual({ accountId: 810 })
    expect(values.has('robopark:interface:v1:810')).toBe(false)
  })

  it('does not expose a mode mutation API for authenticated accounts', () => {
    const { store, values } = setup()
    store.setAccount(101)
    expect(values.size).toBe(0)
    expect('requestMode' in store).toBe(false)
  })

  it('keeps only account identity in presentation state', () => {
    const { store } = setup()
    store.setAccount(101)
    expect(store.getSnapshot()).toEqual({ accountId: 101 })
    expect('beginMutation' in store).toBe(false)
  })

  it('survives unavailable browser storage', () => {
    const store = createInterfaceModeStore(() => { throw new Error('denied') })
    store.setAccount(1)
    expect(store.getSnapshot()).toEqual({ accountId: 1 })
  })
})
