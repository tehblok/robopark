export type InterfaceMode = 'classic' | 'task-first'
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
export type InterfaceStorage = Pick<Storage, 'getItem' | 'setItem'>

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
  const apply = (mode: InterfaceMode) => {
    if (accountId !== null) {
      try { storage().setItem(`robopark:interface:v1:${accountId}`, mode) } catch { /* Memory-only when storage is unavailable. */ }
    }
    publish({ ...snapshot, accountId, mode, pendingMode: null })
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener) } },
    setAccount(nextAccount) {
      if (nextAccount === accountId) return
      accountId = nextAccount
      generation++
      let mode: InterfaceMode = 'classic'
      if (accountId !== null) {
        try {
          if (storage().getItem(`robopark:interface:v1:${accountId}`) === 'task-first') mode = 'task-first'
        } catch { /* No preference may block login. */ }
      }
      publish({ accountId, mode, pendingMode: null, mutationCount: 0 })
    },
    requestMode(mode) {
      if (snapshot.mutationCount > 0) {
        publish({ ...snapshot, pendingMode: mode === snapshot.mode ? null : mode })
      } else apply(mode)
    },
    beginMutation() {
      const startedGeneration = generation
      let released = false
      publish({ ...snapshot, mutationCount: snapshot.mutationCount + 1 })
      return () => {
        if (released || startedGeneration !== generation) return
        released = true
        const mutationCount = snapshot.mutationCount - 1
        const pending = snapshot.pendingMode
        publish({ ...snapshot, mutationCount })
        if (mutationCount === 0 && pending) apply(pending)
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
