import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, PageShell } from '../../components/PageShell'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { systemClient, type HostCapabilities, type HostOperationContext, type SystemClient, type SystemHistory, type SystemJob, type SystemSummary } from '../../opsApi'
import { SystemMetrics } from './SystemMetrics'
import { SystemOperations } from './SystemOperations'
import { OtaUpdatePanel } from './ota/OtaUpdatePanel'
import { PrivilegedSecurityPanel } from './PrivilegedSecurityPanel'
import { BotSettingsPanel } from './BotSettingsPanel'
import { BotConfigPanel } from './BotConfigPanel'
import { clearOperationReservation, readOperationReservation } from './operationReservation'
import './system.css'

const POLL_MS = 30_000
function readFailure(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = typeof error.detail === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(error.detail)
      ? `: ${error.detail}` : ''
    return `HTTP ${error.status}${detail}`
  }
  return 'Ошибка соединения'
}

function reconcilingJob(id: string): SystemJob {
  return { id, kind: '', state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null }
}

export function SystemPage({ client = systemClient }: { client?: SystemClient }) {
  const { user } = useAuth()
  const allowed = user?.role === 'admin' || user?.role === 'royal'
  const royal = user?.role === 'royal'
  const actorScope = user ? JSON.stringify([user.id, user.username, user.role, user.access_status]) : ''
  const [dataScope, setDataScope] = useState<string | null>(null)
  const [errorScope, setErrorScope] = useState<string | null>(null)
  const [summary, setSummary] = useState<SystemSummary | null>(null)
  const [history, setHistory] = useState<SystemHistory | null>(null)
  const [capabilities, setCapabilities] = useState<HostCapabilities | null>(null)
  const [operationContext, setOperationContext] = useState<HostOperationContext | null>(null)
  const [operationHistory, setOperationHistory] = useState<SystemJob[]>([])
  const [job, setJob] = useState<SystemJob | null>(() => {
    const stored = user ? readOperationReservation(user) : null
    return stored ? reconcilingJob(stored.id) : null
  })
  const [failed, setFailed] = useState(false)
  const [dataError, setDataError] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [capabilityError, setCapabilityError] = useState<string | null>(null)
  const [operationError, setOperationError] = useState<string | null>(null)
  const mounted = useRef(false)
  const pending = useRef<Promise<void> | null>(null)
  const postingOperation = useRef<string | null>(null)
  const jobGeneration = useRef(0)
  const historyReady = useRef(false)
  const currentScope = useRef(actorScope)

  useLayoutEffect(() => {
    if (currentScope.current !== actorScope) {
      currentScope.current = actorScope
      jobGeneration.current++
      historyReady.current = false
      postingOperation.current = null
      pending.current = null
    }
  }, [actorScope])

  const clearProtected = useCallback(() => {
    jobGeneration.current++
    historyReady.current = false
    setDataScope(null)
    setSummary(null); setHistory(null); setCapabilities(null); setOperationContext(null); setOperationHistory([]); setJob(null); setCapabilityError(null); setOperationError(null); setHistoryError(null)
  }, [])
  const refreshCapabilities = useCallback(async () => {
    if (!royal) return
    try {
      const value = await client.getCapabilities()
      if (currentScope.current !== actorScope) return
      setCapabilities(value); setCapabilityError(null)
    } catch (caught) {
      if (currentScope.current !== actorScope) return
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) clearProtected()
      else { setCapabilities(null); setCapabilityError(readFailure(caught)) }
    }
  }, [actorScope, clearProtected, client, royal])
  const acceptJob = useCallback((value: SystemJob) => {
    if (currentScope.current === actorScope) {
      jobGeneration.current++
      setJob(value)
      setOperationHistory(current => [value, ...current.filter(item => item.id !== value.id)].slice(0, 20))
    }
  }, [actorScope])
  const markPosting = useCallback((id: string | null) => {
    if (currentScope.current === actorScope) postingOperation.current = id
  }, [actorScope])
  const refresh = useCallback((background = false) => {
    if (!allowed) return Promise.resolve()
    if (pending.current) return pending.current
    const work = (async () => {
      try {
        const refreshJobGeneration = jobGeneration.current
        const stored = royal && user ? readOperationReservation(user) : null
        const royalReads = royal
          ? Promise.all([
              client.getCapabilities()
                .then(value => ({ value, error: null }))
                .catch(caught => {
                  if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) throw caught
                  return { value: null, error: readFailure(caught) }
                }),
              stored
                ? postingOperation.current === stored.id
                  ? Promise.resolve({ state: 'posting' as const })
                  : client.getOperation(stored.id).then(value => ({ state: 'found' as const, value })).catch(caught => {
                    if (caught instanceof ApiError && caught.status === 404) return { state: 'absent' as const }
                    if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) throw caught
                    return { state: 'error' as const, error: readFailure(caught) }
                  })
                : Promise.resolve({ state: 'none' as const }),
              client.getOperations().then(value => ({ value, error: null })).catch(caught => {
                if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) throw caught
                return { value: null, error: readFailure(caught) }
              }),
              client.getOperationContext().then(value => ({ value, error: null })).catch(caught => {
                if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) throw caught
                return { value: null, error: readFailure(caught) }
              }),
            ])
          : Promise.resolve([{ value: null, error: null }, { state: 'none' as const }, { value: null, error: null }, { value: null, error: null }] as const)
        const historyRead = background && historyReady.current ? Promise.resolve(null) : client.getHistory()
          .then(value => ({ value, error: null }))
          .catch(caught => {
            if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) throw caught
            return { value: null, error: readFailure(caught) }
          })
        const summaryRead = client.getSummary().then(value => {
          if (mounted.current && currentScope.current === actorScope) {
            setDataScope(actorScope)
            setSummary(value)
            setFailed(false)
            setErrorScope(null)
            setDataError(null)
          }
          return value
        })
        const [, historyResult, [capabilityResult, operation, operationsResult, contextResult]] = await Promise.all([
          summaryRead, historyRead, royalReads,
        ])
        if (!mounted.current || currentScope.current !== actorScope) return
        if (historyResult) {
          historyReady.current = historyResult.value !== null
          setHistory(historyResult.value); setHistoryError(historyResult.error)
        }
        if (royal) {
          setCapabilities(capabilityResult.value)
          setCapabilityError(capabilityResult.error)
          setOperationContext(contextResult.value)
          if (refreshJobGeneration === jobGeneration.current) {
            if (operationsResult.value) setOperationHistory(operationsResult.value.items)
            setOperationError(operation.state === 'error' ? operation.error : operationsResult.error ?? contextResult.error)
            if (stored && operation.state === 'found') {
              setJob(operation.value)
              if (operation.value.receipt_state === 'terminal' || operation.value.state === 'succeeded' || operation.value.state === 'failed') clearOperationReservation(user)
            } else if (stored && operation.state === 'absent') {
              setJob({ id: stored.id, kind: stored.kind, state: 'queued', phase: 'Запрос не подтверждён — повторите тот же запрос', progress_percent: 0, error: null })
            } else if (stored && operation.state === 'error') {
              setJob(reconcilingJob(stored.id))
            } else if (!stored) {
              setJob((operationsResult.value?.items ?? []).find(item => item.state === 'queued' || item.state === 'running') ?? null)
            }
          }
        }
      } catch (caught) {
        if (!mounted.current || currentScope.current !== actorScope) return
        setFailed(true)
        setErrorScope(actorScope)
        setDataError(readFailure(caught))
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) clearProtected()
      }
    })().finally(() => { if (pending.current === work) pending.current = null })
    pending.current = work
    return work
  }, [actorScope, allowed, clearProtected, client, royal, user])

  useEffect(() => {
    mounted.current = true
    void refresh()
    let timer: number | undefined
    const clear = () => { window.clearTimeout(timer); timer = undefined }
    const schedule = () => {
      clear()
      if (!document.hidden) timer = window.setTimeout(async () => { await refresh(true); schedule() }, POLL_MS)
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
  const visible = dataScope === actorScope
  const pageSummary = visible ? summary : null
  const pageHistory = visible ? history : null
  const pageCapabilities = visible ? capabilities : null
  const pageOperationContext = visible ? operationContext : null
  const pageOperationHistory = visible ? operationHistory : []
  const pageJob = visible ? job : null
  const scopedFailed = failed && errorScope === actorScope
  return <PageShell title="Система" subtitle="Состояние Robopark и управляемые операции.">
    {scopedFailed && <Alert tone="error">Не удалось получить свежие данные: {dataError}. Повторная проверка продолжится после восстановления связи.</Alert>}
    {visible && historyError && <Alert tone="error">Не удалось получить историю: {historyError}. Повторная проверка продолжится после восстановления связи.</Alert>}
    {visible && capabilityError && <Alert tone="error">Host bridge недоступен: {capabilityError}. Управляемые операции отключены.</Alert>}
    {visible && operationError && <Alert tone="error">Не удалось получить состояние операции: {operationError}. Повторная проверка продолжится после восстановления связи.</Alert>}
    {pageSummary && <nav aria-label="Разделы системы" className="rp-system-jump">
      <a href="#system-status">Состояние</a>
      <a href="#system-storage">Занятое место</a>
      {royal && user && <a href="#system-security">Безопасность</a>}
      {royal && user && <a href="#system-bot">Telegram-бот</a>}
      {royal && user && pageCapabilities && <a href="#system-operations">Обслуживание</a>}
      {royal && <a href="/terminal.html">Терминал хоста</a>}
    </nav>}
    {!pageSummary ? !scopedFailed && <LoadingState label="Загружаем состояние системы" variant="page" /> : <SystemMetrics history={pageHistory} summary={pageSummary} />}
    {royal && user && <div className="rp-system-anchor" id="system-security"><PrivilegedSecurityPanel username={user.username} /></div>}
    {royal && user && <div className="rp-system-anchor rp-system-bot-panels" id="system-bot"><BotSettingsPanel /><BotConfigPanel /></div>}
    {royal && user && pageCapabilities && <div className="rp-system-operation-group" id="system-operations"><OtaUpdatePanel
      actor={user} capabilities={pageCapabilities} currentBuildId={typeof pageSummary?.release?.build_id === 'string' ? pageSummary.release.build_id : null}
      currentVersion={typeof pageSummary?.release?.version === 'string' ? pageSummary.release.version : null}
      job={pageJob} onAccepted={acceptJob} onPostingChange={markPosting} onRefreshCapabilities={refreshCapabilities} system={client}
    />
    <SystemOperations actor={user} key={`${user.id}:${user.username}:${pageCapabilities.revision ?? pageCapabilities.generated_at}`} capabilities={pageCapabilities} client={client} context={pageOperationContext} history={pageOperationHistory} job={pageJob} onAccepted={acceptJob} onPostingChange={markPosting} onRefreshCapabilities={refreshCapabilities} /></div>}
  </PageShell>
}
