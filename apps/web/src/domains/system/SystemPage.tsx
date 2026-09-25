import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, PageShell } from '../../components/PageShell'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { systemClient, type HostCapabilities, type SystemClient, type SystemHistory, type SystemJob, type SystemSummary } from '../../opsApi'
import { SystemMetrics } from './SystemMetrics'
import { SystemOperations } from './SystemOperations'
import { clearOperationReservation, readOperationReservation } from './operationReservation'
import './system.css'

const POLL_MS = 30_000
function reconcilingJob(id: string): SystemJob {
  return { id, kind: '', state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null }
}

export function SystemPage({ client = systemClient }: { client?: SystemClient }) {
  const { user } = useAuth()
  const allowed = user?.role === 'admin' || user?.role === 'royal'
  const royal = user?.role === 'royal'
  const [summary, setSummary] = useState<SystemSummary | null>(null)
  const [history, setHistory] = useState<SystemHistory | null>(null)
  const [capabilities, setCapabilities] = useState<HostCapabilities | null>(null)
  const [job, setJob] = useState<SystemJob | null>(() => {
    const stored = readOperationReservation()
    return stored ? reconcilingJob(stored.id) : null
  })
  const [failed, setFailed] = useState(false)
  const mounted = useRef(false)
  const pending = useRef<Promise<void> | null>(null)
  const postingOperation = useRef<string | null>(null)

  const clearProtected = useCallback(() => {
    setSummary(null); setHistory(null); setCapabilities(null); setJob(null)
  }, [])
  const refreshCapabilities = useCallback(async () => {
    if (!royal) return
    try { setCapabilities(await client.getCapabilities()) } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) clearProtected()
      else setCapabilities(null)
    }
  }, [clearProtected, client, royal])
  const refresh = useCallback(() => {
    if (!allowed) return Promise.resolve()
    if (pending.current) return pending.current
    const work = (async () => {
      try {
        const stored = royal ? readOperationReservation() : null
        const royalReads = royal
          ? Promise.all([
              client.getCapabilities(),
              stored
                ? postingOperation.current === stored.id
                  ? Promise.resolve({ state: 'posting' as const })
                  : client.getOperation(stored.id).then(value => ({ state: 'found' as const, value })).catch(caught => {
                    if (caught instanceof ApiError && caught.status === 404) return { state: 'absent' as const }
                    throw caught
                  })
                : Promise.resolve({ state: 'none' as const }),
            ])
          : Promise.resolve([null, { state: 'none' as const }] as const)
        const [nextSummary, nextHistory, [nextCapabilities, operation]] = await Promise.all([
          client.getSummary(), client.getHistory(), royalReads,
        ])
        if (!mounted.current) return
        setSummary(nextSummary); setHistory(nextHistory); setFailed(false)
        if (royal) {
          setCapabilities(nextCapabilities ?? null)
          if (stored && operation.state === 'found') {
            setJob(operation.value)
            if (operation.value.receipt_state === 'terminal' || operation.value.state === 'succeeded' || operation.value.state === 'failed') clearOperationReservation()
          } else if (stored && operation.state === 'absent') {
            setJob({ id: stored.id, kind: stored.kind, state: 'queued', phase: 'Запрос не подтверждён — повторите тот же запрос', progress_percent: 0, error: null })
          }
        }
      } catch (caught) {
        if (!mounted.current) return
        setFailed(true)
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) clearProtected()
      }
    })().finally(() => { if (pending.current === work) pending.current = null })
    pending.current = work
    return work
  }, [allowed, clearProtected, client, royal])

  useEffect(() => {
    mounted.current = true
    void refresh()
    let timer: number | undefined
    const clear = () => { window.clearTimeout(timer); timer = undefined }
    const schedule = () => {
      clear()
      if (!document.hidden) timer = window.setTimeout(async () => { await refresh(); schedule() }, POLL_MS)
    }
    const resume = () => {
      clear()
      if (!document.hidden) void refresh().finally(schedule)
    }
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('focus', resume)
    schedule()
    return () => {
      mounted.current = false; clear(); pending.current = null
      document.removeEventListener('visibilitychange', resume); window.removeEventListener('focus', resume)
    }
  }, [refresh])

  if (!allowed) return <PageShell title="Система"><Alert tone="error">Доступ к системному разделу закрыт.</Alert></PageShell>
  return <PageShell title="Система" subtitle="Состояние Robopark и управляемые операции без доступа к командной строке.">
    {failed && <Alert tone="warning">Не удалось получить свежие данные. Повторная проверка продолжится после восстановления связи.</Alert>}
    {!summary || !history ? <LoadingState label="Загружаем состояние системы" variant="page" /> : <SystemMetrics history={history} summary={summary} />}
    {royal && capabilities && <SystemOperations key={capabilities.revision ?? capabilities.generated_at} capabilities={capabilities} client={client} job={job} onAccepted={setJob} onPostingChange={id => { postingOperation.current = id }} onRefreshCapabilities={refreshCapabilities} />}
  </PageShell>
}
