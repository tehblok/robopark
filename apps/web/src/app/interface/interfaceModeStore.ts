export type InterfaceMode = 'classic'
export type InterfaceModeSnapshot = {
  accountId: number | null
  mode: InterfaceMode
  pendingMode: InterfaceMode | null
  mutationCount: number
}
export interface InterfaceModeStore {
  getSnapshot(): InterfaceModeSnapshot
  subscribe(listener: () => void): () => void
  setAccount(accountId: number | null): void
  requestMode(mode: InterfaceMode): void
  beginMutation(): () => void
}
export type InterfaceStorage = Pick<Storage, 'getItem' | 'removeItem'>

export function createInterfaceModeStore(storage: () => InterfaceStorage): InterfaceModeStore {
  let accountId: number | null = null
  let generation = 0
  let snapshot: InterfaceModeSnapshot = { accountId: null, mode: 'classic', pendingMode: null, mutationCount: 0 }
  const listeners = new Set<() => void>()
  const publish = (next: InterfaceModeSnapshot) => {
    if (next.accountId === snapshot.accountId && next.mode === snapshot.mode
      && next.pendingMode === snapshot.pendingMode && next.mutationCount === snapshot.mutationCount) return
    snapshot = next
    for (const listener of listeners) listener()
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener) } },
    setAccount(nextAccount) {
      if (nextAccount === accountId) return
      accountId = nextAccount
      generation++
      if (accountId !== null) {
        try {
          const key = `robopark:interface:v1:${accountId}`
          if (storage().getItem(key) !== null) storage().removeItem(key)
        } catch { /* No preference may block login. */ }
      }
      publish({ accountId, mode: 'classic', pendingMode: null, mutationCount: 0 })
    },
    requestMode() {},
    beginMutation() {
      const startedGeneration = generation
      let released = false
      publish({ ...snapshot, mutationCount: snapshot.mutationCount + 1 })
      return () => {
        if (released || startedGeneration !== generation) return
        released = true
        const mutationCount = snapshot.mutationCount - 1
        publish({ ...snapshot, mutationCount })
      }
    },
  }
}

export const interfaceModeStore = createInterfaceModeStore(() => window.localStorage)

export async function trackInterfaceMutation<T>(
  store: InterfaceModeStore, method: string, operation: () => Promise<T>,
): Promise<T> {
  const readOnly = ['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
  const release = readOnly ? () => {} : store.beginMutation()
  try { return await operation() } finally { release() }
}
