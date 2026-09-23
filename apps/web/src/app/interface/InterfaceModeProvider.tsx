import { createContext, useContext, useLayoutEffect, useSyncExternalStore, type PropsWithChildren } from 'react'
import { interfaceModeStore, type InterfaceModeStore } from './interfaceModeStore'
import { PresentationModeContext } from './presentationModeContext'

const InterfaceStoreContext = createContext(interfaceModeStore)

export function InterfaceModeProvider({ children, accountId, store = interfaceModeStore }: PropsWithChildren<{
  accountId: number | null
  store?: InterfaceModeStore
}>) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  useLayoutEffect(() => { store.setAccount(accountId) }, [accountId, store])
  const accountReady = accountId !== null && snapshot.accountId === accountId
  const presentationMode = accountReady ? 'classic' : null
  useLayoutEffect(() => {
    if (presentationMode) document.documentElement.dataset.interface = presentationMode
    else delete document.documentElement.dataset.interface
  }, [presentationMode])
  return <InterfaceStoreContext.Provider value={store}>
    <PresentationModeContext.Provider value={presentationMode}>
      {children}
    </PresentationModeContext.Provider>
  </InterfaceStoreContext.Provider>
}

export function useInterfaceMode() {
  const store = useContext(InterfaceStoreContext)
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return { ...snapshot, requestMode: store.requestMode }
}
