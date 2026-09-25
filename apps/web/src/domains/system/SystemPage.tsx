import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, PageShell } from '../../components/PageShell'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { systemClient, type HostCapabilities, type SystemClient, type SystemHistory, type SystemJob, type SystemSummary } from '../../opsApi'
import { SystemMetrics } from './SystemMetrics'
import { SystemOperations } from './SystemOperations'
import './system.css'

const POLL_MS = 30_000

export function SystemPage({ client = systemClient }: { client?: SystemClient }) {
  const { user } = useAuth()
  const allowed = user?.role === 'admin' || user?.role === 'royal'
  const royal = user?.role === 'royal'
  const [summary, setSummary] = useState<SystemSummary | null>(null)
  const [history, setHistory] = useState<SystemHistory | null>(null)
  const [capabilities, setCapabilities] = useState<HostCapabilities | null>(null)
  const [job, setJob] = useState<SystemJob | null>(null)
  const [failed, setFailed] = useState(false)
  const mounted = useRef(false)
  const pending = useRef<Promise<void> | null>(null)

  const refreshCapabilities = useCallback(async () => {
    if (!royal) return
    try { setCapabilities(await client.getCapabilities()) } catch { setCapabilities(null) }
  }, [client, royal])
  const refresh = useCallback(() => {
    if (!allowed) return Promise.resolve()
    if (pending.current) return pending.current
    const work = (async () => {
      try {
        const reads: [Promise<SystemSummary>, Promise<SystemHistory>, Promise<HostCapabilities>?, Promise<SystemJob>?] = [client.getSummary(), client.getHistory()]
        if (royal) { reads.push(client.getCapabilities()); reads.push(client.getJob()) }
        const [nextSummary, nextHistory, nextCapabilities, nextJob] = await Promise.all(reads)
        if (!mounted.current) return
        setSummary(nextSummary); setHistory(nextHistory); setFailed(false)
        if (royal) {
          setCapabilities(nextCapabilities ?? null)
          const stored = localStorage.getItem('robopark:system-operation')
          if (nextJob?.id && (!stored || stored === nextJob.id)) {
            setJob(nextJob)
            localStorage.setItem('robopark:system-operation', nextJob.id)
            if (nextJob.state === 'succeeded' || nextJob.state === 'failed') localStorage.removeItem('robopark:system-operation')
          }
        }
      } catch (caught) {
        if (!mounted.current) return
        setFailed(true)
        if (caught instanceof ApiError && caught.status === 403) {
          setSummary(null); setHistory(null); setCapabilities(null); setJob(null)
        }
      }
    })().finally(() => { if (pending.current === work) pending.current = null })
    pending.current = work
    return work
  }, [allowed, client, royal])

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
    {royal && capabilities && <SystemOperations capabilities={capabilities} client={client} job={job} onAccepted={setJob} onRefreshCapabilities={refreshCapabilities} />}
  </PageShell>
}
