import { createContext, useContext, useLayoutEffect, useSyncExternalStore, type PropsWithChildren } from 'react'
import { interfaceModeStore, type InterfaceModeStore } from './interfaceModeStore'

const InterfaceStoreContext = createContext(interfaceModeStore)

export function InterfaceModeProvider({ children, accountId, store = interfaceModeStore }: PropsWithChildren<{
  accountId: number | null
  store?: InterfaceModeStore
}>) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  useLayoutEffect(() => { store.setAccount(accountId) }, [accountId, store])
  useLayoutEffect(() => {
    document.documentElement.dataset.interface = snapshot.mode
  }, [snapshot.mode])
  return <InterfaceStoreContext.Provider value={store}>{children}</InterfaceStoreContext.Provider>
}

export function useInterfaceMode() {
  const store = useContext(InterfaceStoreContext)
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return { ...snapshot, requestMode: store.requestMode }
}
