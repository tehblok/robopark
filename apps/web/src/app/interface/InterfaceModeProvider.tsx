import { createContext, useContext, useEffect, useLayoutEffect, useState, useSyncExternalStore, type PropsWithChildren } from 'react'
import { interfaceModeStore, type InterfaceModeStore } from './interfaceModeStore'
import { loadInterfaceStyles } from './loadInterfaceStyles'

const InterfaceStoreContext = createContext(interfaceModeStore)

export function InterfaceModeProvider({ children, accountId, store = interfaceModeStore }: PropsWithChildren<{
  accountId: number | null
  store?: InterfaceModeStore
}>) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [stylesReady, setStylesReady] = useState(false)
  const [styleError, setStyleError] = useState(false)
  useLayoutEffect(() => { store.setAccount(accountId) }, [accountId, store])
  useEffect(() => {
    if (snapshot.mode !== 'task-first' || stylesReady) return
    let current = true
    void loadInterfaceStyles().then(() => {
      if (current) { setStylesReady(true); setStyleError(false) }
    }).catch(() => {
      if (current) { setStyleError(true); store.requestMode('classic') }
    })
    return () => { current = false }
  }, [snapshot.mode, stylesReady, store])
  useLayoutEffect(() => {
    document.documentElement.dataset.interface = snapshot.mode === 'task-first' && stylesReady ? 'task-first' : 'classic'
  }, [snapshot.mode, stylesReady])
  return <InterfaceStoreContext.Provider value={store}>
    {styleError ? <div className="rp-interface-notice" role="alert">Новый интерфейс не загрузился. Включён Классический — введённые данные сохранены.
      <button type="button" onClick={() => setStyleError(false)}>Понятно</button>
    </div> : null}
    {children}
  </InterfaceStoreContext.Provider>
}

export function useInterfaceMode() {
  const store = useContext(InterfaceStoreContext)
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return { ...snapshot, requestMode: store.requestMode }
}
