export type InterfaceMode = 'classic'
export type InterfaceModeSnapshot = {
  accountId: number | null
}
export interface InterfaceModeStore {
  getSnapshot(): InterfaceModeSnapshot
  subscribe(listener: () => void): () => void
  setAccount(accountId: number | null): void
}
export type InterfaceStorage = Pick<Storage, 'getItem' | 'removeItem'>

export function createInterfaceModeStore(storage: () => InterfaceStorage): InterfaceModeStore {
  let accountId: number | null = null
  let snapshot: InterfaceModeSnapshot = { accountId: null }
  const listeners = new Set<() => void>()
  const publish = (next: InterfaceModeSnapshot) => {
    if (next.accountId === snapshot.accountId) return
    snapshot = next
    for (const listener of listeners) listener()
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener) } },
    setAccount(nextAccount) {
      if (nextAccount === accountId) return
      accountId = nextAccount
      if (accountId !== null) {
        try {
          const key = `robopark:interface:v1:${accountId}`
          if (storage().getItem(key) !== null) storage().removeItem(key)
        } catch { /* No preference may block login. */ }
      }
      publish({ accountId })
    },
  }
}

export const interfaceModeStore = createInterfaceModeStore(() => window.localStorage)
