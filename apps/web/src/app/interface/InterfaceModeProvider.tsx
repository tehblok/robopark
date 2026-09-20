import { createContext, useContext, useEffect, useLayoutEffect, useState, useSyncExternalStore, type PropsWithChildren } from 'react'
import { interfaceModeStore, type InterfaceModeStore } from './interfaceModeStore'
import { loadInterfaceStyles } from './loadInterfaceStyles'
import { PresentationModeContext } from './presentationModeContext'

const InterfaceStoreContext = createContext(interfaceModeStore)

export function InterfaceModeProvider({ children, accountId, store = interfaceModeStore }: PropsWithChildren<{
  accountId: number | null
  store?: InterfaceModeStore
}>) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const [stylesReady, setStylesReady] = useState(false)
  const [styleError, setStyleError] = useState(false)
  const [painted, setPainted] = useState<{ accountId: number; mode: 'classic' | 'task-first' } | null>(null)
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
  const accountReady = accountId !== null && snapshot.accountId === accountId
  const presentationMode = !accountReady ? null
    : snapshot.mode === 'classic' ? 'classic'
      : stylesReady ? 'task-first'
        : painted?.accountId === accountId ? painted.mode : null
  useLayoutEffect(() => {
    if (presentationMode) {
      if (painted?.accountId !== accountId || painted.mode !== presentationMode) {
        setPainted({ accountId: accountId as number, mode: presentationMode })
      }
    } else if (accountId === null || snapshot.accountId !== accountId) {
      if (painted) setPainted(null)
    }
  }, [accountId, painted, presentationMode, snapshot.accountId])
  useLayoutEffect(() => {
    if (presentationMode) document.documentElement.dataset.interface = presentationMode
    else delete document.documentElement.dataset.interface
  }, [presentationMode])
  return <InterfaceStoreContext.Provider value={store}>
    <PresentationModeContext.Provider value={presentationMode}>
      {styleError ? <div className="rp-interface-notice" role="alert">Новый интерфейс не загрузился. Включён Классический — введённые данные сохранены.
        <button type="button" onClick={() => setStyleError(false)}>Понятно</button>
      </div> : null}
      {children}
    </PresentationModeContext.Provider>
  </InterfaceStoreContext.Provider>
}

export function useInterfaceMode() {
  const store = useContext(InterfaceStoreContext)
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return { ...snapshot, requestMode: store.requestMode }
}
