import { describe, expect, it } from 'vitest'
import { createInterfaceModeStore, trackInterfaceMutation, type InterfaceStorage } from './interfaceModeStore'

function setup() {
  const values = new Map<string, string>()
  const storage: InterfaceStorage = {
    getItem: key => values.get(key) ?? null,
    removeItem: key => { values.delete(key) },
  }
  return { values, store: createInterfaceModeStore(() => storage) }
}

describe('classic interface lifecycle', () => {
  it('removes a stored legacy preference and resolves the account to classic', () => {
    const { store, values } = setup()
    values.set('robopark:interface:v1:810', ['task', 'first'].join('-'))

    store.setAccount(810)

    expect(store.getSnapshot().mode).toBe('classic')
    expect(values.has('robopark:interface:v1:810')).toBe(false)
  })

  it('does not persist a mode preference for authenticated accounts', () => {
    const { store, values } = setup()
    store.setAccount(101)
    store.requestMode('classic')
    expect(values.size).toBe(0)
    expect(store.getSnapshot().mode).toBe('classic')
  })

  it('survives unavailable browser storage', () => {
    const store = createInterfaceModeStore(() => { throw new Error('denied') })
    store.setAccount(1)
    expect(store.getSnapshot().mode).toBe('classic')
  })

  it('tracks all writes and ignores duplicate release', () => {
    const { store } = setup()
    store.setAccount(101)
    const first = store.beginMutation()
    const second = store.beginMutation()
    expect(store.getSnapshot().mutationCount).toBe(2)
    first(); first()
    expect(store.getSnapshot().mutationCount).toBe(1)
    second()
    expect(store.getSnapshot().mutationCount).toBe(0)
  })

  it('does not release another account writes', () => {
    const { store } = setup()
    store.setAccount(1)
    const oldRelease = store.beginMutation()
    store.setAccount(2)
    const newRelease = store.beginMutation()
    oldRelease()
    expect(store.getSnapshot()).toEqual({ accountId: 2, mode: 'classic', pendingMode: null, mutationCount: 1 })
    newRelease()
    expect(store.getSnapshot().mutationCount).toBe(0)
  })

  it('keeps a write pending until consumption completes and releases failures', async () => {
    const { store } = setup()
    let finish!: () => void
    const operation = trackInterfaceMutation(store, 'post', () => new Promise<void>(resolve => { finish = resolve }))
    expect(store.getSnapshot().mutationCount).toBe(1)
    finish()
    await operation
    expect(store.getSnapshot().mutationCount).toBe(0)
    await expect(trackInterfaceMutation(store, 'DELETE', async () => { throw new Error('offline') })).rejects.toThrow('offline')
    expect(store.getSnapshot().mutationCount).toBe(0)
  })

  it.each(['GET', 'HEAD', 'OPTIONS'])('does not track %s reads', async method => {
    const { store } = setup()
    await trackInterfaceMutation(store, method, async () => {
      expect(store.getSnapshot().mutationCount).toBe(0)
    })
  })
})
