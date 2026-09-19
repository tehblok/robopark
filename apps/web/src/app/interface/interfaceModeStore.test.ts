import { describe, expect, it } from 'vitest'
import { createInterfaceModeStore, trackInterfaceMutation, type InterfaceStorage } from './interfaceModeStore'

function setup() {
  const values = new Map<string, string>()
  const storage: InterfaceStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value) } }
  return { values, store: createInterfaceModeStore(() => storage) }
}

describe('interface selection lifecycle', () => {
  it('defaults to classic and persists only the authenticated account choice', () => {
    const { store, values } = setup()
    expect(store.getSnapshot().mode).toBe('classic')
    store.requestMode('task-first')
    expect(values.size).toBe(0)
    store.setAccount(101)
    expect(store.getSnapshot().mode).toBe('classic')
    store.requestMode('task-first')
    expect(values.get('robopark:interface:v1:101')).toBe('task-first')
    store.setAccount(202)
    expect(store.getSnapshot().mode).toBe('classic')
    store.setAccount(101)
    expect(store.getSnapshot().mode).toBe('task-first')
  })

  it('survives unavailable browser storage', () => {
    const store = createInterfaceModeStore(() => { throw new Error('denied') })
    store.setAccount(1)
    store.requestMode('task-first')
    expect(store.getSnapshot().mode).toBe('task-first')
    store.setAccount(1)
    expect(store.getSnapshot().mode).toBe('task-first')
  })

  it('waits for all writes and ignores duplicate release', () => {
    const { store } = setup()
    store.setAccount(101)
    const first = store.beginMutation()
    const second = store.beginMutation()
    store.requestMode('task-first')
    expect(store.getSnapshot()).toEqual({ mode: 'classic', pendingMode: 'task-first', mutationCount: 2 })
    first(); first()
    expect(store.getSnapshot().mode).toBe('classic')
    expect(store.getSnapshot().mutationCount).toBe(1)
    second()
    expect(store.getSnapshot()).toEqual({ mode: 'task-first', pendingMode: null, mutationCount: 0 })
  })

  it('cancels a deferred choice when the current mode is selected again', () => {
    const { store } = setup()
    const release = store.beginMutation()
    store.requestMode('task-first')
    store.requestMode('classic')
    release()
    expect(store.getSnapshot().mode).toBe('classic')
    expect(store.getSnapshot().pendingMode).toBeNull()
  })

  it('does not release another account writes or apply the old pending choice', () => {
    const { store } = setup()
    store.setAccount(1)
    const oldRelease = store.beginMutation()
    store.requestMode('task-first')
    store.setAccount(2)
    const newRelease = store.beginMutation()
    oldRelease()
    expect(store.getSnapshot()).toEqual({ mode: 'classic', pendingMode: null, mutationCount: 1 })
    newRelease()
    expect(store.getSnapshot().mutationCount).toBe(0)
  })

  it('notifies only changes and unsubscribes', () => {
    const { store } = setup()
    const before = store.getSnapshot()
    let updates = 0
    const unsubscribe = store.subscribe(() => updates++)
    store.requestMode('classic')
    expect(store.getSnapshot()).toBe(before)
    expect(updates).toBe(0)
    store.requestMode('task-first')
    expect(updates).toBe(1)
    unsubscribe()
    store.requestMode('classic')
    expect(updates).toBe(1)
  })

  it('keeps a write pending until consumption completes and releases failures', async () => {
    const { store } = setup()
    let finish!: () => void
    const operation = trackInterfaceMutation(store, 'post', () => new Promise<void>(resolve => { finish = resolve }))
    store.requestMode('task-first')
    expect(store.getSnapshot().mode).toBe('classic')
    finish()
    await operation
    expect(store.getSnapshot().mode).toBe('task-first')
    await expect(trackInterfaceMutation(store, 'DELETE', async () => { throw new Error('offline') })).rejects.toThrow('offline')
    expect(store.getSnapshot().mutationCount).toBe(0)
  })

  it.each(['GET', 'HEAD', 'OPTIONS'])('does not defer a choice for %s reads', async method => {
    const { store } = setup()
    await trackInterfaceMutation(store, method, async () => {
      store.requestMode('task-first')
      expect(store.getSnapshot().mode).toBe('task-first')
    })
  })
})
