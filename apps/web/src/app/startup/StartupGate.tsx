import { type PropsWithChildren, useEffect, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { Spinner } from '../../components/ui/Feedback'
import { initializeApplicationStartup } from './startup'

export function StartupGate({
  children,
  initialize = initializeApplicationStartup,
}: PropsWithChildren<{ initialize?: () => Promise<void> }>) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<'starting' | 'ready' | 'error'>('starting')

  useEffect(() => {
    let current = true
    setState('starting')
    void initialize().then(
      () => { if (current) setState('ready') },
      () => { if (current) setState('error') },
    )
    return () => { current = false }
  }, [attempt, initialize])

  if (state === 'ready') return children
  return (
    <main className="page page-center">
      {state === 'starting' ? (
        <div aria-live="polite" role="status"><Spinner label="Запускаем локальное приложение…" /></div>
      ) : (
        <div className="empty-block" role="alert">
          <p className="empty-block-title">Локальные данные пока недоступны</p>
          <p className="empty-block-hint">Проверьте, что служба Robopark запущена, и повторите подключение.</p>
          <div className="empty-block-action">
            <Button onClick={() => setAttempt(value => value + 1)} variant="secondary">Повторить</Button>
          </div>
        </div>
      )}
    </main>
  )
}
