import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Alert } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { TabPanel, Tabs } from '../../design-system/navigation/Tabs'
import {
  assistantApi, type AiAction, type AiConfig, type AiJob, type AiPrompt, type AiStatus,
  type AssistantApiClient, type AssistantScript, type Automation, type AutomationRun,
  type Connector, type Conversation, type ConversationDetail,
} from './assistantApi'
import { assistantErrorText, statusReasonText } from './assistantUi'
import './assistant.css'

type Tab = 'chat' | 'automations' | 'scripts' | 'connectors' | 'settings'
const MANAGER_ROLES = new Set(['admin', 'royal'])

const errorText = assistantErrorText

function useLoader<T>(load: (signal: AbortSignal) => Promise<T>, dependencies: readonly unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const activeController = useRef<AbortController | null>(null)
  const refresh = useCallback(async () => {
    activeController.current?.abort()
    const controller = new AbortController()
    activeController.current = controller
    const currentGeneration = ++generation.current
    setLoading(true); setError(null)
    try {
      const next = await load(controller.signal)
      if (currentGeneration !== generation.current || controller.signal.aborted) return null
      setData(next); return next
    } catch (caught) {
      if (currentGeneration === generation.current && !controller.signal.aborted) setError(errorText(caught))
      return null
    } finally {
      if (currentGeneration === generation.current) {
        if (activeController.current === controller) activeController.current = null
        if (!controller.signal.aborted) setLoading(false)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies)
  useEffect(() => {
    void refresh()
    return () => {
      generation.current += 1
      activeController.current?.abort()
      activeController.current = null
    }
  }, [refresh])
  return { data, setData, loading, error, setError, refresh }
}

type JobWaitResult = { job: AiJob; timedOut: boolean }

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const rejectAborted = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
    if (signal.aborted) { rejectAborted(); return }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve() }, milliseconds)
    const onAbort = () => { window.clearTimeout(timer); rejectAborted() }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

async function waitForJob(api: AssistantApiClient, initial: AiJob, signal: AbortSignal, onProgress?: (job: AiJob) => void): Promise<JobWaitResult> {
  let current = initial
  for (let attempt = 0; attempt < 180 && (current.state === 'queued' || current.state === 'running'); attempt += 1) {
    if (attempt) await abortableDelay(1000, signal)
    signal.throwIfAborted()
    current = await api.job(current.id, signal)
    signal.throwIfAborted()
    onProgress?.(current)
  }
  return { job: current, timedOut: current.state === 'queued' || current.state === 'running' }
}

export function AssistantPage({ apiClient = assistantApi }: { apiClient?: AssistantApiClient }) {
  const { user } = useAuth()
  const identity = JSON.stringify([user?.id ?? null, user?.role ?? null, user?.access_status ?? null, [...(user?.permissions ?? [])].sort()])
  return <AssistantWorkspace apiClient={apiClient} key={identity} />
}

function AssistantWorkspace({ apiClient }: { apiClient: AssistantApiClient }) {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  const [search] = useSearchParams()
  const requestedParkId = Number(search.get('park_id'))
  const effectiveParkId = Number.isInteger(requestedParkId) && user?.parks.some(park => park.id === requestedParkId)
    ? requestedParkId
    : parkId
  const status = useLoader(signal => apiClient.status(signal), [apiClient, user?.id])
  const statusSupported = status.data?.supported
  const setStatus = status.setData
  const setStatusError = status.setError
  const statusPollController = useRef<AbortController | null>(null)
  useEffect(() => {
    if (!statusSupported) return
    let cancelled = false
    const poll = async () => {
      if (cancelled || statusPollController.current) return
      const controller = new AbortController()
      statusPollController.current = controller
      try {
        const next = await apiClient.status(controller.signal)
        if (!cancelled && !controller.signal.aborted) setStatus(next)
      } catch (caught) {
        if (!cancelled && !controller.signal.aborted && caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
          // Unmount protected panels and abort their requests as soon as access
          // is denied. A transient network/server failure can keep the draft.
          setStatus(null)
          setStatusError(errorText(caught))
        }
      } finally {
        if (statusPollController.current === controller) statusPollController.current = null
      }
    }
    const timer = window.setInterval(poll, 5000)
    return () => {
      cancelled = true; window.clearInterval(timer)
      statusPollController.current?.abort()
      statusPollController.current = null
    }
  }, [apiClient, statusSupported, setStatus, setStatusError, user?.id])
  const [tab, setTab] = useState<Tab>('chat')
  const [openedTabs, setOpenedTabs] = useState(() => new Set<Tab>(['chat']))
  const openTab = (id: string) => {
    const next = id as Tab
    setOpenedTabs(previous => new Set([...previous, next]))
    setTab(next)
  }
  const canManage = Boolean(user && MANAGER_ROLES.has(user.role) && status.data?.can_manage)
  const userScope = user?.id ?? 'anonymous'
  const parkScope = `${userScope}:${effectiveParkId ?? 'all'}`
  const tabs = useMemo(() => [
    { id: 'chat', label: 'Помощник' },
    ...(canManage ? [
      { id: 'automations', label: 'Автоматизации' }, { id: 'scripts', label: 'Скрипты' },
      { id: 'connectors', label: 'Подключения' }, { id: 'settings', label: 'Настройки и журнал' },
    ] : []),
  ], [canManage])

  if (status.loading) return <LoadingState label="Проверяем локальный помощник" />
  if (!status.data) return <ErrorState title="Не удалось получить статус помощника" description={status.error ?? 'Нет ответа сервера'} onRetry={() => void status.refresh()} />
  if (!status.data.supported) return <UnsupportedStatus status={status.data} />

  return <PageLayout className="rp-assistant" title="Локальный помощник" description="Помощник использует доступные вам задачи, диагностику и инструменты. Для ответов нужна подключённая локальная модель.">
    {!status.data.ready ? <Alert tone="warning">{statusReasonText(status.data.reason)}</Alert> : null}
    <Tabs ariaLabel="Разделы помощника" items={tabs} value={tab} onChange={openTab} panelIdFor={id => `assistant-panel-${id}`} wrapOnPhone />
    <TabPanel id="assistant-panel-chat" labelledBy="tab-chat" active={tab === 'chat'}><ChatPanel api={apiClient} enabled={status.data.ready} issueKey={search.get('issue_key')} key={`chat:${parkScope}`} parkId={effectiveParkId} /></TabPanel>
    {canManage ? <>
      <TabPanel id="assistant-panel-automations" labelledBy="tab-automations" active={tab === 'automations'}>{openedTabs.has('automations') ? <AutomationsPanel api={apiClient} modelReady={status.data.ready} key={`automations:${parkScope}`} parkId={effectiveParkId} /> : null}</TabPanel>
      <TabPanel id="assistant-panel-scripts" labelledBy="tab-scripts" active={tab === 'scripts'}>{openedTabs.has('scripts') ? <ScriptsPanel api={apiClient} modelReady={status.data.ready} key={`scripts:${parkScope}`} parkId={effectiveParkId} /> : null}</TabPanel>
      <TabPanel id="assistant-panel-connectors" labelledBy="tab-connectors" active={tab === 'connectors'}>{openedTabs.has('connectors') ? <ConnectorsPanel api={apiClient} key={`connectors:${userScope}`} /> : null}</TabPanel>
      <TabPanel id="assistant-panel-settings" labelledBy="tab-settings" active={tab === 'settings'}>{openedTabs.has('settings') ? <SettingsPanel api={apiClient} key={`settings:${userScope}`} status={status.data} onStatus={status.setData} /> : null}</TabPanel>
    </> : null}
  </PageLayout>
}

function UnsupportedStatus({ status }: { status: AiStatus }) {
  return <PageLayout className="rp-assistant" title="Локальный помощник"><Panel>
    <h2>Требуется NVIDIA AGX Orin</h2>
    <p>Локальная модель запускается только на подтверждённом AGX Orin с NVMe и CUDA. На этом устройстве чат и фоновые задания помощника недоступны.</p>
    {status.reason ? <p className="rp-assistant-muted">{statusReasonText(status.reason)}</p> : null}
  </Panel></PageLayout>
}

function ChatPanel({ api, enabled, parkId, issueKey }: { api: AssistantApiClient; enabled: boolean; parkId: number | null; issueKey: string | null }) {
  const sessions = useLoader(signal => api.conversations(signal), [api])
  const sessionData = sessions.data; const sessionsLoading = sessions.loading; const setSessionData = sessions.setData
  const sessionsReady = !sessionsLoading && sessionData !== null
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [opening, setOpening] = useState(false)
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState(new Set<string>())
  const [deciding, setDeciding] = useState(false)
  const decisionController = useRef<AbortController | null>(null)
  const [activeJob, setActiveJob] = useState<AiJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const initializedContext = useRef<string | null>(null)
  const resumedJob = useRef<string | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  const deleteControllers = useRef(new Map<string, AbortController>())
  const resumePollController = useRef<AbortController | null>(null)
  const openGeneration = useRef(0)
  const openController = useRef<AbortController | null>(null)
  const createController = useRef<AbortController | null>(null)
  const submitPollController = useRef<AbortController | null>(null)

  const select = useCallback((id: string | null) => { selectedIdRef.current = id; setSelectedId(id) }, [])
  const stopConversationRequests = useCallback(() => {
    openController.current?.abort(); openController.current = null
    submitPollController.current?.abort(); submitPollController.current = null
    resumePollController.current?.abort(); resumePollController.current = null
    resumedJob.current = null
    decisionController.current?.abort(); decisionController.current = null; setDeciding(false)
  }, [])
  const open = useCallback(async (id: string) => {
    const currentGeneration = ++openGeneration.current
    createController.current?.abort(); createController.current = null; setCreating(false)
    stopConversationRequests()
    const controller = new AbortController()
    openController.current = controller
    select(id); setDetail(null); setActiveJob(null); setSending(false); setOpening(true); setError(null)
    try {
      const next = await api.conversation(id, controller.signal)
      if (currentGeneration === openGeneration.current && !controller.signal.aborted) setDetail(next)
    } catch (caught) {
      if (currentGeneration === openGeneration.current && !controller.signal.aborted) setError(errorText(caught))
    } finally {
      if (openController.current === controller) {
        openController.current = null
        if (!controller.signal.aborted) setOpening(false)
      }
    }
  }, [api, select, stopConversationRequests])
  const create = useCallback(async (parentSignal?: AbortSignal) => {
    if (createController.current || parentSignal?.aborted) return null
    if (parkId == null) { setError('Сначала выберите парк.'); return null }
    const controller = new AbortController()
    const generation = openGeneration.current
    createController.current = controller
    setCreating(true); setError(null)
    const current = () => !controller.signal.aborted && !parentSignal?.aborted && generation === openGeneration.current
    try {
      const conversation = await api.createConversation({ park_id: parkId, ...(issueKey ? { issue_key: issueKey, title: issueKey } : {}) })
      if (!current()) return null
      setSessionData(previous => [conversation, ...(previous ?? []).filter(item => item.id !== conversation.id)])
      resumePollController.current?.abort(); resumePollController.current = null; resumedJob.current = null
      select(conversation.id); setDetail({ ...conversation, messages: [], jobs: [] }); setActiveJob(null)
      return conversation
    } catch (caught) {
      if (current()) setError(`Не удалось создать разговор: ${errorText(caught)}`)
      return null
    } finally {
      if (createController.current === controller) {
        createController.current = null
        if (current()) setCreating(false)
      }
    }
  }, [api, issueKey, parkId, select, setSessionData])
  useEffect(() => () => {
    openGeneration.current += 1
    stopConversationRequests()
    createController.current?.abort()
    for (const controller of deleteControllers.current.values()) controller.abort()
    deleteControllers.current.clear()
  }, [stopConversationRequests])
  useEffect(() => {
    const context = issueKey && parkId != null ? `${parkId}:${issueKey}` : 'default'
    if (sessionsLoading || !sessionData) return
    if (initializedContext.current === context) return
    initializedContext.current = context
    const generation = ++openGeneration.current
    stopConversationRequests()
    createController.current?.abort(); createController.current = null
    select(null); setDetail(null); setActiveJob(null); setError(null)
    setCreating(false); setOpening(false); setSending(false)
    if (issueKey && parkId != null) {
      const match = sessionData.find(item => item.issue_key === issueKey && item.park_id === parkId)
      if (match) void open(match.id)
      else if (enabled) {
        void create().then(conversation => {
          if (conversation && generation === openGeneration.current && initializedContext.current === context) void open(conversation.id)
        })
      }
    } else if (sessionData.length) {
      void open(sessionData[0].id)
    }
  }, [create, enabled, issueKey, open, parkId, select, sessionData, sessionsLoading, stopConversationRequests])

  const pendingJob = detail?.jobs.findLast(job => ['queued', 'running', 'waiting'].includes(job.state))
  useEffect(() => {
    if (!pendingJob || resumedJob.current === pendingJob.id) return
    const controller = new AbortController()
    resumePollController.current = controller
    resumedJob.current = pendingJob.id; setActiveJob(pendingJob)
    void waitForJob(api, pendingJob, controller.signal, setActiveJob).then(async result => {
      if (controller.signal.aborted) return
      if (result.timedOut) { setActiveJob(result.job); return }
      setActiveJob(result.job)
      if (detail) {
        const next = await api.conversation(detail.id, controller.signal)
        if (!controller.signal.aborted) setDetail(next)
      }
      if (result.job.state === 'failed') setError(errorText(result.job.error || 'Генерация завершилась с ошибкой'))
    }).catch(caught => {
      if (controller.signal.aborted) return
      setActiveJob(null); setError(errorText(caught))
    })
    return () => {
      controller.abort()
      if (resumePollController.current === controller) resumePollController.current = null
    }
  }, [api, detail, pendingJob])

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (actionPending || !draft.trim() || !enabled || !sessionsReady || creating || sending || opening || submitPollController.current) return
    const controller = new AbortController()
    submitPollController.current = controller
    setSending(true); setError(null)
    try {
      const conversation = detail ?? await create(controller.signal); if (!conversation || controller.signal.aborted) return
      const job = await api.sendMessage(conversation.id, draft.trim(), crypto.randomUUID())
      if (controller.signal.aborted) return
      setActiveJob(job)
      const result = await waitForJob(api, job, controller.signal, setActiveJob)
      if (controller.signal.aborted) return
      if (result.timedOut) { setActiveJob(result.job); return }
      setActiveJob(result.job)
      const next = await api.conversation(conversation.id, controller.signal)
      if (!controller.signal.aborted) { setDetail(next); setDraft('') }
      if (result.job.state === 'waiting') return
      if (result.job.state !== 'succeeded') throw new Error(result.job.error || (result.job.state === 'cancelled' ? 'Задание отменено' : 'Генерация завершилась с ошибкой'))
    } catch (caught) {
      if (!controller.signal.aborted) { setActiveJob(null); setError(`Не удалось отправить: ${errorText(caught)}`) }
    } finally {
      if (!controller.signal.aborted) setSending(false)
      if (submitPollController.current === controller) submitPollController.current = null
    }
  }
  const remove = async (session: Conversation) => {
    if (deleteControllers.current.has(session.id)) return
    const controller = new AbortController()
    deleteControllers.current.set(session.id, controller)
    setDeleting(previous => new Set(previous).add(session.id)); setError(null)
    try {
      await api.deleteConversation(session.id)
      if (controller.signal.aborted) return
      setSessionData(previous => previous?.filter(item => item.id !== session.id) ?? null)
      if (selectedIdRef.current === session.id) {
        stopConversationRequests()
        select(null); setDetail(null); setActiveJob(null); setSending(false); setOpening(false)
      }
    } catch (caught) {
      if (!controller.signal.aborted) setError(`Не удалось удалить разговор «${session.title}»: ${errorText(caught)}`)
    } finally {
      if (deleteControllers.current.get(session.id) === controller) deleteControllers.current.delete(session.id)
      if (!controller.signal.aborted) setDeleting(previous => { const next = new Set(previous); next.delete(session.id); return next })
    }
  }
  const decide = async (job: AiJob, action?: AiAction) => {
    if (decisionController.current || (action && !action.digest)) return
    const controller = new AbortController()
    const conversationId = selectedIdRef.current
    decisionController.current = controller; setDeciding(true); setError(null)
    try {
      const next = action ? await api.confirmAction(action.id, action.digest!) : await api.cancelJob(job.id)
      if (controller.signal.aborted || selectedIdRef.current !== conversationId) return
      resumedJob.current = null
      setActiveJob(next)
      setDetail(previous => previous?.id === conversationId ? { ...previous, jobs: [...previous.jobs.filter(item => item.id !== next.id), next] } : previous)
    } catch (caught) {
      if (!controller.signal.aborted && selectedIdRef.current === conversationId) setError(errorText(caught))
    } finally {
      if (decisionController.current === controller) { decisionController.current = null; setDeciding(false) }
    }
  }
  const displayedJobs = [...(detail?.jobs ?? []).filter(job => job.id !== activeJob?.id), ...(activeJob ? [activeJob] : [])]
  const actionPending = displayedJobs.some(job => ['waiting', 'queued', 'running'].includes(job.state))
  const selectedSession = sessions.data?.find(item => item.id === selectedId)

  return <div className="rp-assistant-chat-layout">
    <Panel title="Разговоры" actions={<Button busy={creating} disabled={!enabled || !sessionsReady || creating || sending || opening || parkId == null} onClick={() => void create()} size="compact">Новый</Button>}>
      {sessions.loading ? <LoadingState label="Загружаем разговоры" /> : sessions.error ? <ErrorState title="Не удалось загрузить разговоры" description={sessions.error} onRetry={() => void sessions.refresh()} /> :
        sessions.data?.length ? <ul className="rp-assistant-list">{sessions.data.map(item => <li key={item.id}><button aria-current={selectedId === item.id} onClick={() => void open(item.id)} type="button"><strong>{item.title}</strong>{item.issue_key ? <span>{item.issue_key}</span> : null}</button><Button aria-label={`Удалить ${item.title}`} busy={deleting.has(item.id)} disabled={deleting.has(item.id)} onClick={() => void remove(item)} size="compact" variant="ghost">×</Button></li>)}</ul> : <p>Начните новый разговор.</p>}
    </Panel>
    <Panel title={detail?.title ?? selectedSession?.title ?? (issueKey ? `Помощник по ${issueKey}` : 'Новый разговор')}>
      {error ? <Alert tone="error">{error}</Alert> : null}
      <div aria-live="polite" className="rp-assistant-messages">{creating ? <LoadingState label="Создаём разговор" /> : opening ? <LoadingState label="Открываем разговор" /> : detail?.messages.length ? detail.messages.map(message => <article className={`rp-assistant-message rp-assistant-message--${message.role}`} key={message.id}><strong>{message.role === 'assistant' ? 'Помощник' : 'Вы'}</strong><p>{message.content}</p>{message.sources.length ? <div className="rp-assistant-sources"><span>Источники</span>{message.sources.map(source => <span key={source.id} title={source.excerpt}>{source.title}</span>)}</div> : null}</article>) : <EmptyState title="Задайте вопрос по ремонту" description={issueKey ? `Контекст задачи ${issueKey} будет приложен к разговору.` : 'Выберите парк и опишите симптом или нужную процедуру.'} />}</div>
      <ActionReceipts jobs={displayedJobs} busy={deciding} enabled={enabled} onDecision={decide} />
      {activeJob && (activeJob.state === 'queued' || activeJob.state === 'running') ? <div className="rp-assistant-job" role="status"><span>{sending ? 'Готовим ответ…' : 'Ответ ещё готовится. Можно вернуться позже.'}</span><Button busy={deciding} onClick={() => void decide(activeJob)} size="compact" variant="secondary">Отменить</Button></div> : null}
      <form className="rp-assistant-compose" onSubmit={submit}><FormField id="assistant-message" label="Сообщение помощнику"><textarea disabled={!enabled || !sessionsReady || creating || sending || opening || actionPending} onChange={event => setDraft(event.target.value)} rows={3} value={draft} /></FormField><Button busy={sending} disabled={!enabled || !sessionsReady || creating || !draft.trim() || parkId == null || opening || actionPending} type="submit">Отправить</Button></form>
    </Panel>
  </div>
}

const ACTION_STATES: Record<AiAction['state'], string> = {
  ready: 'Подготовлено', waiting: 'Нужно подтверждение', approved: 'Подтверждено', running: 'Выполняется',
  succeeded: 'Выполнено', failed: 'Не выполнено', cancelled: 'Отменено', uncertain: 'Результат требует проверки',
}
function actionStateLabel(action: AiAction): string {
  if (action.state === 'succeeded' && action.result && typeof action.result === 'object'
    && 'accepted' in action.result && action.result.accepted === true) return 'Принято в обработку'
  if (action.state === 'succeeded' && action.tool.startsWith('task_') && action.tool !== 'task_get'
    && action.result && typeof action.result === 'object' && 'sync_state' in action.result
    && action.result.sync_state !== 'synced') return 'Принято в обработку'
  return ACTION_STATES[action.state]
}

function ActionReceipts({ jobs, busy, enabled, onDecision }: { jobs: AiJob[]; busy: boolean; enabled: boolean; onDecision: (job: AiJob, action?: AiAction) => Promise<void> }) {
  if (!jobs.some(job => job.actions?.length)) return null
  return <div className="rp-assistant-receipts" role="group" aria-label="Действия помощника">{jobs.flatMap(job => (job.actions ?? []).map(action => <article className="rp-assistant-advanced" key={action.id}>
    <strong>{actionStateLabel(action)}</strong><p>{action.preview}</p>
    {action.state === 'uncertain' ? <p>Вызов мог завершиться. Проверьте объект перед повторной командой.</p> : null}
    {action.error ? <p>{errorText(action.error)}</p> : null}
    {action.arguments ? <details><summary>Параметры действия</summary><pre>{JSON.stringify(action.arguments, null, 2)}</pre></details> : null}
    {action.result != null ? <details><summary>Результат</summary><pre>{JSON.stringify(action.result, null, 2)}</pre></details> : null}
    {action.state === 'waiting' && action.digest ? <><p>Подтвердите именно это действие. Если данные изменятся, оно будет остановлено.</p><div className="rp-assistant-actions">
      <Button busy={busy} disabled={busy || !enabled} onClick={() => void onDecision(job, action)}>Подтвердить действие</Button>
      <Button disabled={busy} onClick={() => void onDecision(job)} variant="secondary">Отменить действие</Button>
    </div></> : null}
  </article>))}</div>
}

function DraftGenerator({ api, kind, parkId, modelReady, onProposal }: { api: AssistantApiClient; modelReady: boolean; kind: 'script' | 'automation'; parkId: number | null; onProposal: (result: Record<string, unknown>) => void }) {
  const [instruction, setInstruction] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [proposal, setProposal] = useState<Record<string, unknown> | null>(null); const [copied, setCopied] = useState(false)
  const pollController = useRef<AbortController | null>(null)
  useEffect(() => () => pollController.current?.abort(), [])
  const generate = async () => { if (!modelReady || parkId == null || !instruction.trim()) return; const controller = new AbortController(); pollController.current?.abort(); pollController.current = controller; setBusy(true); setError(null); setCopied(false); try { const { job: complete, timedOut } = await waitForJob(api, await api.createDraft({ kind, instruction: instruction.trim(), park_id: parkId }), controller.signal); if (controller.signal.aborted) return; if (timedOut || complete.state !== 'succeeded' || !complete.result || typeof complete.result !== 'object') throw new Error(complete.error || (timedOut ? 'Черновик ещё готовится. Попробуйте открыть раздел позже.' : 'Черновик не создан')); const next = complete.result as Record<string, unknown>; setProposal(next); onProposal(next) } catch (caught) { if (!controller.signal.aborted) setError(errorText(caught)) } finally { if (!controller.signal.aborted) setBusy(false); if (pollController.current === controller) pollController.current = null } }
  const copy = async () => { if (!proposal) return; try { await navigator.clipboard.writeText(JSON.stringify(proposal, null, 2)); setCopied(true) } catch { setError('Не удалось скопировать предложение. Выделите текст вручную.') } }
  return <details className="rp-assistant-advanced"><summary>Создать черновик с помощником</summary><FormField id={`draft-${kind}`} label="Что нужно сделать"><textarea onChange={event => setInstruction(event.target.value)} rows={3} value={instruction} /></FormField>{error ? <Alert tone="error">{error}</Alert> : null}<Button busy={busy} disabled={!modelReady || parkId == null || !instruction.trim()} onClick={() => void generate()}>Подготовить предложение</Button>{proposal ? <><pre>{JSON.stringify(proposal, null, 2)}</pre><Button onClick={() => void copy()} variant="secondary">Копировать предложение</Button>{copied ? <span role="status">Скопировано.</span> : null}</> : null}<p>Предложение перенесено в редактор, но не сохраняется и не включается автоматически.</p></details>
}

function ScriptsPanel({ api, parkId, modelReady }: { api: AssistantApiClient; parkId: number | null; modelReady: boolean }) {
  const resource = useLoader(signal => api.scripts(signal), [api]); const [selected, setSelected] = useState<AssistantScript | null>(null); const [name, setName] = useState(''); const [source, setSource] = useState('def main(data):\n    return {}'); const [input, setInput] = useState('{}'); const [result, setResult] = useState(''); const [error, setError] = useState<string | null>(null)
  const testPollController = useRef<AbortController | null>(null)
  useEffect(() => () => testPollController.current?.abort(), [])
  const select = (item: AssistantScript) => { setSelected(item); setName(item.name); setSource(item.source); setResult('') }
  const save = async () => { setError(null); try { const next = selected ? await api.updateScript(selected.id, { name, source, revision: selected.revision }) : await api.createScript({ name, source }); setSelected(next); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const test = async () => { if (!selected) return; const controller = new AbortController(); testPollController.current?.abort(); testPollController.current = controller; setError(null); try { const parsed = JSON.parse(input) as unknown; const { job: complete, timedOut } = await waitForJob(api, await api.testScript(selected.id, parsed), controller.signal); if (controller.signal.aborted) return; setResult(JSON.stringify(complete.result, null, 2)); if (timedOut || complete.state !== 'succeeded') throw new Error(complete.error || (timedOut ? 'Проверка ещё выполняется' : 'Проверка не пройдена')); const all = await resource.refresh(); const current = all?.find(item => item.id === selected.id); if (current) select(current) } catch (caught) { if (!controller.signal.aborted) setError(errorText(caught)) } finally { if (testPollController.current === controller) testPollController.current = null } }
  const toggle = async () => { if (!selected) return; try { const next = await api.updateScript(selected.id, { revision: selected.revision, enabled: !selected.enabled }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Скрипты" actions={<Button onClick={() => { setSelected(null); setName(''); setSource('def main(data):\n    return {}') }} size="compact">Новый</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => select(item)} type="button"><strong>{item.name}</strong><span>r{item.revision} · {item.enabled ? 'включён' : 'выключен'} · тест r{item.tested_revision ?? '—'}</span></button></li>)}</ul> : <EmptyState title="Скриптов пока нет" />}<DraftGenerator api={api} modelReady={modelReady} kind="script" parkId={parkId} onProposal={proposal => { setSelected(null); setName('Черновик'); setSource(String(proposal.source ?? '')) }} /></Panel><Panel title={selected ? selected.name : 'Редактор скрипта'}>{error ? <Alert tone="error">{error}</Alert> : null}<FormField id="script-name" label="Название"><input onChange={event => setName(event.target.value)} value={name} /></FormField><FormField id="script-source" label="Python: main(data) → JSON"><textarea className="rp-assistant-code" onChange={event => setSource(event.target.value)} rows={16} value={source} /></FormField><div className="rp-assistant-actions"><Button disabled={!name.trim() || !source.trim()} onClick={() => void save()}>Сохранить</Button>{selected ? <><Button onClick={() => void toggle()} variant="secondary">{selected.enabled ? 'Выключить' : 'Включить'}</Button><Button onClick={() => { if (window.confirm(`Удалить скрипт «${selected.name}»?`)) void api.deleteScript(selected.id).then(() => { setSelected(null); void resource.refresh() }) }} variant="danger">Удалить</Button></> : null}</div>{selected ? <details className="rp-assistant-advanced" open><summary>Проверка текущей ревизии</summary><FormField id="script-input" label="Входной JSON"><textarea className="rp-assistant-code" onChange={event => setInput(event.target.value)} rows={5} value={input} /></FormField><Button onClick={() => void test()}>Запустить тест</Button>{result ? <pre>{result}</pre> : null}</details> : null}</Panel></div>
}

function ConnectorsPanel({ api }: { api: AssistantApiClient }) {
  const resource = useLoader(signal => api.connectors(signal), [api]); const [selected, setSelected] = useState<Connector | null>(null); const [error, setError] = useState<string | null>(null)
  const edit = (item: Connector | null) => setSelected(item)
  const save = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const value = { name: String(data.get('name')), url: String(data.get('url')), method: String(data.get('method')) as Connector['method'], token: String(data.get('token') || '') || undefined, enabled: data.get('enabled') === 'on' }; setError(null); try { const next = selected ? await api.updateConnector(selected.id, { ...value, revision: selected.revision }) : await api.createConnector(value); setSelected(next); await resource.refresh(); form.reset() } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Подключения" actions={<Button onClick={() => edit(null)} size="compact">Новое</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => edit(item)} type="button"><strong>{item.name}</strong><span>{item.method} · {item.enabled ? 'включено' : 'выключено'} · токен {item.token_set ? 'задан' : 'не задан'}</span></button></li>)}</ul> : <EmptyState title="Подключений пока нет" />}</Panel><Panel title={selected ? `Подключение: ${selected.name}` : 'Новое подключение'}><p>Адрес фиксируется администратором. Секрет после сохранения не отображается.</p>{error ? <Alert tone="error">{error}</Alert> : null}<form className="rp-assistant-form" key={selected?.id ?? 'new'} onSubmit={save}><FormField id="connector-name" label="Название" required><input defaultValue={selected?.name} name="name" /></FormField><FormField id="connector-url" label="HTTPS адрес" required><input defaultValue={selected?.url} inputMode="url" name="url" placeholder="https://service.example/issues/{{issue_key}}" type="text" /></FormField><p className="rp-assistant-muted">Шаблон {'{{issue_key}}'} разрешён только в пути, например https://service.example/issues/{'{{issue_key}}'}.</p><FormField id="connector-method" label="Метод"><select defaultValue={selected?.method ?? 'POST'} name="method"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option></select></FormField><FormField id="connector-token" label={selected?.token_set ? 'Новый bearer-токен (необязательно)' : 'Bearer-токен'}><input autoComplete="new-password" name="token" type="password" /></FormField><label><input defaultChecked={selected?.enabled} name="enabled" type="checkbox" /> Включено</label><div className="rp-assistant-actions"><Button type="submit">Сохранить</Button>{selected ? <Button onClick={() => { if (window.confirm(`Удалить подключение «${selected.name}»?`)) void api.deleteConnector(selected.id).then(() => { setSelected(null); void resource.refresh() }) }} type="button" variant="danger">Удалить</Button> : null}</div></form></Panel></div>
}

function AutomationsPanel({ api, parkId, modelReady }: { api: AssistantApiClient; parkId: number | null; modelReady: boolean }) {
  const resource = useLoader(signal => api.automations(signal), [api]); const [selected, setSelected] = useState<Automation | null>(null); const [name, setName] = useState(''); const [filters, setFilters] = useState('{"component_ids":[],"defect_codes":[],"keywords":[]}'); const [action, setAction] = useState('{"body":{}}'); const [event, setEvent] = useState('{}'); const [output, setOutput] = useState(''); const [error, setError] = useState<string | null>(null)
  const select = (item: Automation) => { setSelected(item); setName(item.name); setFilters(JSON.stringify(item.filters, null, 2)); setAction(JSON.stringify(item.action, null, 2)) }
  const save = async () => { if (parkId == null) return; setError(null); try { const parsedFilters = JSON.parse(filters) as Automation['filters'], parsedAction = JSON.parse(action) as Automation['action']; const next = selected ? await api.updateAutomation(selected.id, { revision: selected.revision, name, filters: parsedFilters, action: parsedAction }) : await api.createAutomation({ name, park_id: parkId, filters: parsedFilters, action: parsedAction }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const toggle = async () => { if (!selected) return; try { const next = await api.updateAutomation(selected.id, { revision: selected.revision, enabled: !selected.enabled }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const preview = async () => { if (!selected) return; try { const result = await api.previewAutomation(selected.id, JSON.parse(event) as unknown); setOutput(JSON.stringify(result, null, 2)) } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Правила" actions={<Button onClick={() => { setSelected(null); setName(''); setFilters('{"component_ids":[],"defect_codes":[],"keywords":[]}'); setAction('{"body":{}}') }} size="compact">Новое</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => select(item)} type="button"><strong>{item.name}</strong><span>{item.enabled ? 'включено' : 'выключено'} · r{item.revision}</span></button></li>)}</ul> : <EmptyState title="Автоматизаций пока нет" />}<DraftGenerator api={api} modelReady={modelReady} kind="automation" parkId={parkId} onProposal={proposal => { setSelected(null); setName('Черновик'); setFilters(JSON.stringify((proposal.proposal as Record<string, unknown>)?.filters ?? {}, null, 2)); setAction(JSON.stringify((proposal.proposal as Record<string, unknown>)?.action ?? {}, null, 2)); setOutput(String(proposal.explanation ?? '')) }} /></Panel><Panel title={selected ? selected.name : 'Редактор правила'}>{error ? <Alert tone="error">{error}</Alert> : null}<FormField id="automation-name" label="Название"><input onChange={e => setName(e.target.value)} value={name} /></FormField><details className="rp-assistant-advanced" open><summary>Фильтры и действие</summary><FormField id="automation-filters" label="Фильтры JSON"><textarea className="rp-assistant-code" onChange={e => setFilters(e.target.value)} rows={7} value={filters} /></FormField><FormField id="automation-action" label="Действие JSON"><textarea className="rp-assistant-code" onChange={e => setAction(e.target.value)} rows={7} value={action} /></FormField></details><div className="rp-assistant-actions"><Button disabled={!name.trim() || parkId == null} onClick={() => void save()}>Сохранить выключенным</Button>{selected ? <><Button onClick={() => void toggle()} variant="secondary">{selected.enabled ? 'Выключить' : 'Включить'}</Button><Button onClick={() => void api.deleteAutomation(selected.id).then(() => { setSelected(null); void resource.refresh() })} variant="danger">Удалить</Button></> : null}</div>{selected ? <details className="rp-assistant-advanced"><summary>Пробный прогон без внешнего запроса</summary><FormField id="automation-event" label="Событие JSON"><textarea className="rp-assistant-code" onChange={e => setEvent(e.target.value)} rows={6} value={event} /></FormField><Button onClick={() => void preview()}>Проверить</Button>{output ? <pre>{output}</pre> : null}</details> : output ? <pre>{output}</pre> : null}</Panel></div>
}

function SettingsPanel({ api, status, onStatus }: { api: AssistantApiClient; status: AiStatus; onStatus: (status: AiStatus) => void }) {
  const config = useLoader(signal => api.config(signal), [api]); const prompts = useLoader(signal => api.prompts(signal), [api]); const runs = useLoader(signal => api.runs(50, signal), [api]); const [error, setError] = useState<string | null>(null); const [message, setMessage] = useState<string | null>(null); const [runtimeBusy, setRuntimeBusy] = useState<null | 'install' | 'enable' | 'disable' | 'remove_model'>(null); const runtimeBusyRef = useRef(false); const [cleanupBusy, setCleanupBusy] = useState<string | null>(null); const cleanupBusyRef = useRef(false)
  const updateConfig = async (patch: Partial<AiConfig>) => { if (!config.data) return; try { config.setData(await api.updateConfig({ ...config.data, ...patch, learning_enabled: false })) } catch (caught) { setError(errorText(caught)) } }
  const runtime = async (action: 'install' | 'enable' | 'disable' | 'remove_model') => { if (runtimeBusyRef.current || (action === 'remove_model' && !window.confirm('Удалить локальную модель? Для повторного запуска потребуется повторно подключить файл модели.'))) return; runtimeBusyRef.current = true; setRuntimeBusy(action); setError(null); try { onStatus(await api.runtime(action)) } catch (caught) { setError(errorText(caught)) } finally { runtimeBusyRef.current = false; setRuntimeBusy(null) } }
  const cleanup = async (kind: 'runs' | 'failed_jobs' | 'history') => {
    if (cleanupBusyRef.current) return
    cleanupBusyRef.current = true
    setCleanupBusy(kind)
    setError(null)
    setMessage(null)
    try {
      const result = kind === 'runs' ? await api.purgeRuns(30) : await api.maintenance(kind, 30)
      if (kind === 'runs') {
        setMessage(`Очищено запусков: ${result.deleted}; событий: ${result.events_cleaned ?? 0}`)
        void runs.refresh()
      } else if (kind === 'failed_jobs') {
        setMessage(`Удалено заданий: ${result.deleted}`)
      } else if (result.jobs_deleted === undefined) {
        setMessage(`Удалено бесед: ${result.deleted}`)
      } else {
        setMessage(`Удалено бесед: ${result.conversations_deleted ?? result.deleted}; заданий: ${result.jobs_deleted}; сообщений: ${result.messages_deleted ?? 0}`)
      }
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      cleanupBusyRef.current = false
      setCleanupBusy(null)
    }
  }
  const runtimeDisabled = runtimeBusy !== null
  return <div className="rp-assistant-settings">{error ? <Alert tone="error">{error}</Alert> : null}{message ? <Alert tone="success">{message}</Alert> : null}<Panel title="Модель и запуск"><dl className="rp-assistant-status"><div><dt>Модель</dt><dd>{status.reason === 'awaiting_model' ? 'Ожидается обученная модель' : status.model}</dd></div><div><dt>Среда запуска</dt><dd>{(status.runtime_installed ?? status.installed) ? 'подготовлена' : 'не подготовлена'}</dd></div><div><dt>Файл модели</dt><dd>{(status.model_available ?? status.installed) ? 'подключён' : 'ожидается'}</dd></div><div><dt>Исполнение</dt><dd>{status.ready ? 'готово' : statusReasonText(status.reason)}</dd></div><div><dt>Ускорение</dt><dd>{status.backend ?? '—'}</dd></div><div><dt>Одновременные ответы</dt><dd>{status.parallel_slots ?? 1}</dd></div><div><dt>Источник модели</dt><dd>{!status.installed ? 'Не подключена' : status.model_source === 'registered' ? 'Собственная модель' : 'Встроенная модель'}</dd></div></dl>{config.data ? <><label><input checked={config.data.enabled} onChange={e => void updateConfig({ enabled: e.target.checked })} type="checkbox" /> Разрешить работу помощника</label></> : null}<details className="rp-assistant-advanced"><summary>Управление runtime</summary><div className="rp-assistant-actions"><Button busy={runtimeBusy === 'install'} disabled={runtimeDisabled} onClick={() => void runtime('install')} variant="secondary">Подготовить среду</Button><Button busy={runtimeBusy === 'enable'} disabled={runtimeDisabled || !status.installed} onClick={() => void runtime('enable')} variant="secondary">Включить</Button><Button busy={runtimeBusy === 'disable'} disabled={runtimeDisabled} onClick={() => void runtime('disable')} variant="secondary">Выключить</Button><Button busy={runtimeBusy === 'remove_model'} disabled={runtimeDisabled} onClick={() => void runtime('remove_model')} variant="danger">Удалить модель</Button></div></details></Panel><Panel title="Промпты ролей">{prompts.data?.map(prompt => <PromptEditor api={api} key={prompt.role} prompt={prompt} onSaved={next => prompts.setData((prompts.data ?? []).map(item => item.role === next.role ? next : item))} />)}</Panel><Panel title="Журнал автоматизаций" actions={<Button onClick={() => void runs.refresh()} size="compact" variant="secondary">Обновить</Button>}>{runs.data?.length ? <RunTable runs={runs.data} /> : <EmptyState title="Запусков пока нет" />}<details className="rp-assistant-advanced"><summary>Очистка журналов</summary><div className="rp-assistant-actions"><Button busy={cleanupBusy === 'runs'} disabled={cleanupBusy !== null} onClick={() => void cleanup('runs')} variant="secondary">Очистить запуски старше 30 дней</Button><Button busy={cleanupBusy === 'failed_jobs'} disabled={cleanupBusy !== null} onClick={() => void cleanup('failed_jobs')} variant="secondary">Очистить сбойные задания старше 30 дней</Button><Button busy={cleanupBusy === 'history'} disabled={cleanupBusy !== null} onClick={() => void cleanup('history')} variant="secondary">Очистить историю старше 30 дней</Button></div></details></Panel></div>
}

function PromptEditor({ api, prompt, onSaved }: { api: AssistantApiClient; prompt: AiPrompt; onSaved: (prompt: AiPrompt) => void }) {
  const [content, setContent] = useState(prompt.content); const [error, setError] = useState<string | null>(null)
  return <details className="rp-assistant-advanced"><summary>{prompt.role} · ревизия {prompt.revision}</summary><FormField id={`prompt-${prompt.role}`} label="Инструкции роли"><textarea onChange={e => setContent(e.target.value)} rows={8} value={content} /></FormField>{error ? <Alert tone="error">{error}</Alert> : null}<Button disabled={content === prompt.content} onClick={() => void api.updatePrompt(prompt.role, content, prompt.revision).then(onSaved).catch(caught => setError(errorText(caught)))}>Сохранить промпт</Button><p>Системная политика безопасности остаётся неизменной.</p></details>
}

function RunTable({ runs }: { runs: AutomationRun[] }) {
  return <div className="rp-assistant-table" role="table" aria-label="Запуски автоматизаций">{runs.map(run => <div key={run.id} role="row"><span role="cell">{run.event_key}</span><span role="cell">{run.state}</span><span role="cell">{run.error || new Date(run.created_at).toLocaleString('ru-RU')}</span></div>)}</div>
}
