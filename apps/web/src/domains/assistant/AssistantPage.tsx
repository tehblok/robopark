import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
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
  assistantApi, parseKnowledgeFile, type AiConfig, type AiJob, type AiPrompt, type AiStatus,
  type AssistantApiClient, type AssistantScript, type Automation, type AutomationRun,
  type Connector, type Conversation, type ConversationDetail, type KnowledgeDocument,
  type KnowledgeImportDocument, type KnowledgeKind, type KnowledgeState,
} from './assistantApi'
import { assistantErrorText, createKnowledgeImportBatches, statusReasonText } from './assistantUi'
import './assistant.css'

type Tab = 'chat' | 'knowledge' | 'automations' | 'scripts' | 'connectors' | 'settings'
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
  const [tab, setTab] = useState<Tab>(() => search.has('document') ? 'knowledge' : 'chat')
  useEffect(() => { if (search.has('document')) setTab('knowledge') }, [search])
  const canManage = Boolean(user && MANAGER_ROLES.has(user.role) && status.data?.can_manage)
  const userScope = user?.id ?? 'anonymous'
  const parkScope = `${userScope}:${effectiveParkId ?? 'all'}`
  const tabs = useMemo(() => [
    { id: 'chat', label: 'Помощник' }, { id: 'knowledge', label: 'База знаний' },
    ...(canManage ? [
      { id: 'automations', label: 'Автоматизации' }, { id: 'scripts', label: 'Скрипты' },
      { id: 'connectors', label: 'Подключения' }, { id: 'settings', label: 'Настройки и журнал' },
    ] : []),
  ], [canManage])

  if (status.loading) return <LoadingState label="Проверяем локальный помощник" />
  if (!status.data) return <ErrorState title="Не удалось получить статус помощника" description={status.error ?? 'Нет ответа сервера'} onRetry={() => void status.refresh()} />
  if (!status.data.supported) return <UnsupportedStatus status={status.data} />

  return <PageLayout className="rp-assistant" title="Локальный помощник" description="Ответы по ремонту с проверяемыми источниками. Данные остаются внутри Robopark.">
    {!status.data.ready ? <Alert tone="warning">{statusReasonText(status.data.reason)}</Alert> : null}
    <Tabs ariaLabel="Разделы помощника" items={tabs} value={tab} onChange={id => setTab(id as Tab)} panelIdFor={id => `assistant-panel-${id}`} wrapOnPhone />
    <TabPanel id="assistant-panel-chat" labelledBy="tab-chat" active={tab === 'chat'}><ChatPanel api={apiClient} enabled={status.data.ready} issueKey={search.get('issue_key')} key={`chat:${parkScope}`} parkId={effectiveParkId} /></TabPanel>
    <TabPanel id="assistant-panel-knowledge" labelledBy="tab-knowledge" active={tab === 'knowledge'}><KnowledgePanel api={apiClient} bundle={status.data.knowledge_bundle} canManage={canManage} initialDocumentId={search.get('document')} key={`knowledge:${parkScope}`} parkId={effectiveParkId} /></TabPanel>
    {canManage ? <>
      <TabPanel id="assistant-panel-automations" labelledBy="tab-automations" active={tab === 'automations'}><AutomationsPanel api={apiClient} key={`automations:${parkScope}`} parkId={effectiveParkId} /></TabPanel>
      <TabPanel id="assistant-panel-scripts" labelledBy="tab-scripts" active={tab === 'scripts'}><ScriptsPanel api={apiClient} key={`scripts:${parkScope}`} parkId={effectiveParkId} /></TabPanel>
      <TabPanel id="assistant-panel-connectors" labelledBy="tab-connectors" active={tab === 'connectors'}><ConnectorsPanel api={apiClient} key={`connectors:${userScope}`} /></TabPanel>
      <TabPanel id="assistant-panel-settings" labelledBy="tab-settings" active={tab === 'settings'}><SettingsPanel api={apiClient} key={`settings:${userScope}`} status={status.data} onStatus={status.setData} /></TabPanel>
    </> : null}
  </PageLayout>
}

function UnsupportedStatus({ status }: { status: AiStatus }) {
  return <PageLayout className="rp-assistant" title="Локальный помощник"><Panel>
    <h2>Требуется NVIDIA AGX Orin</h2>
    <p>Локальная модель запускается только на подтверждённом AGX Orin с NVMe и CUDA. На этом устройстве чат, импорт и фоновые задания недоступны.</p>
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

  const pendingJob = detail?.jobs.findLast(job => job.state === 'queued' || job.state === 'running')
  useEffect(() => {
    if (!pendingJob || resumedJob.current === pendingJob.id) return
    const controller = new AbortController()
    resumePollController.current = controller
    resumedJob.current = pendingJob.id; setActiveJob(pendingJob)
    void waitForJob(api, pendingJob, controller.signal, setActiveJob).then(async result => {
      if (controller.signal.aborted) return
      if (result.timedOut) { setActiveJob(result.job); return }
      setActiveJob(null)
      if (result.job.state === 'succeeded' && detail) {
        const next = await api.conversation(detail.id, controller.signal)
        if (!controller.signal.aborted) setDetail(next)
      }
      else if (result.job.state === 'failed') setError(result.job.error || 'Генерация завершилась с ошибкой')
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
    event.preventDefault(); if (!draft.trim() || !enabled || !sessionsReady || creating || sending || opening || submitPollController.current) return
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
      setActiveJob(null)
      if (result.job.state !== 'succeeded') throw new Error(result.job.error || (result.job.state === 'cancelled' ? 'Задание отменено' : 'Генерация завершилась с ошибкой'))
      const next = await api.conversation(conversation.id, controller.signal)
      if (!controller.signal.aborted) { setDetail(next); setDraft('') }
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
  const selectedSession = sessions.data?.find(item => item.id === selectedId)

  return <div className="rp-assistant-chat-layout">
    <Panel title="Разговоры" actions={<Button busy={creating} disabled={!enabled || !sessionsReady || creating || sending || opening || parkId == null} onClick={() => void create()} size="compact">Новый</Button>}>
      {sessions.loading ? <LoadingState label="Загружаем разговоры" /> : sessions.error ? <ErrorState title="Не удалось загрузить разговоры" description={sessions.error} onRetry={() => void sessions.refresh()} /> :
        sessions.data?.length ? <ul className="rp-assistant-list">{sessions.data.map(item => <li key={item.id}><button aria-current={selectedId === item.id} onClick={() => void open(item.id)} type="button"><strong>{item.title}</strong>{item.issue_key ? <span>{item.issue_key}</span> : null}</button><Button aria-label={`Удалить ${item.title}`} busy={deleting.has(item.id)} disabled={deleting.has(item.id)} onClick={() => void remove(item)} size="compact" variant="ghost">×</Button></li>)}</ul> : <p>Начните новый разговор.</p>}
    </Panel>
    <Panel title={detail?.title ?? selectedSession?.title ?? (issueKey ? `Помощник по ${issueKey}` : 'Новый разговор')}>
      {error ? <Alert tone="error">{error}</Alert> : null}
      <div aria-live="polite" className="rp-assistant-messages">{creating ? <LoadingState label="Создаём разговор" /> : opening ? <LoadingState label="Открываем разговор" /> : detail?.messages.length ? detail.messages.map(message => <article className={`rp-assistant-message rp-assistant-message--${message.role}`} key={message.id}><strong>{message.role === 'assistant' ? 'Помощник' : 'Вы'}</strong><p>{message.content}</p>{message.sources.length ? <div className="rp-assistant-sources"><span>Источники</span>{message.sources.map(source => <Link key={source.id} title={source.excerpt} to={`/assistant?document=${encodeURIComponent(source.id)}`}>{source.title}</Link>)}</div> : null}</article>) : <EmptyState title="Задайте вопрос по ремонту" description={issueKey ? `Контекст задачи ${issueKey} будет приложен к разговору.` : 'Выберите парк и опишите симптом или нужную процедуру.'} />}</div>
      {activeJob && (activeJob.state === 'queued' || activeJob.state === 'running') ? <div className="rp-assistant-job" role="status"><span>{sending ? 'Готовим ответ…' : 'Ответ ещё готовится. Можно вернуться позже.'}</span><Button onClick={() => void api.cancelJob(activeJob.id).then(setActiveJob)} size="compact" variant="secondary">Отменить</Button></div> : null}
      <form className="rp-assistant-compose" onSubmit={submit}><FormField id="assistant-message" label="Сообщение помощнику"><textarea disabled={!enabled || !sessionsReady || creating || sending || opening} onChange={event => setDraft(event.target.value)} rows={3} value={draft} /></FormField><Button busy={sending} disabled={!enabled || !sessionsReady || creating || !draft.trim() || parkId == null || opening} type="submit">Отправить</Button></form>
    </Panel>
  </div>
}

function KnowledgeBundleNotice({ bundle }: { bundle: AiStatus['knowledge_bundle'] }) {
  if (!bundle || bundle.state === 'ready') return null
  if (bundle.state === 'importing') {
    const maximum = Math.max(bundle.total, 1)
    const processed = Math.min(Math.max(bundle.processed, 0), maximum)
    return <Alert tone="info">
      Загружаем начальную базу знаний: {bundle.processed} из {bundle.total}.{' '}
      <progress aria-label="Загрузка начальной базы знаний" aria-valuemax={maximum} aria-valuemin={0} aria-valuenow={processed} max={maximum} value={processed} />
    </Alert>
  }
  if (bundle.state === 'failed') return <Alert tone="error">Начальную базу знаний загрузить не удалось. Система повторит попытку; подробности доступны администратору в журнале.</Alert>
  if (bundle.state === 'paused') return <Alert tone="warning">Загрузка начальной базы знаний приостановлена и продолжится после включения помощника.</Alert>
  if (bundle.state === 'unavailable') return <Alert tone="warning">Пакет начальной базы знаний недоступен в этой установке.</Alert>
  return <Alert tone="info">Начальная база знаний ожидает загрузки.</Alert>
}

function KnowledgePanel({ api, bundle, canManage, initialDocumentId, parkId }: { api: AssistantApiClient; bundle: AiStatus['knowledge_bundle']; canManage: boolean; initialDocumentId: string | null; parkId: number | null }) {
  const [query, setQuery] = useState(''); const [state, setState] = useState<KnowledgeState | ''>('')
  const documents = useLoader(signal => api.documents({ q: query || undefined, park_id: parkId ?? undefined, state: state || undefined, signal }), [api, query, parkId, state])
  const [selected, setSelected] = useState<KnowledgeDocument | null>(null); const [editing, setEditing] = useState(false)
  const [message, setMessage] = useState<string | null>(null); const [error, setError] = useState<string | null>(null)
  const documentController = useRef<AbortController | null>(null)
  const open = async (item: KnowledgeDocument) => {
    documentController.current?.abort()
    const controller = new AbortController()
    documentController.current = controller
    setError(null)
    try {
      const next = await api.document(item.id, controller.signal)
      if (!controller.signal.aborted) { setSelected(next); setEditing(false) }
    } catch (caught) { if (!controller.signal.aborted) setError(errorText(caught)) }
  }
  useEffect(() => () => { documentController.current?.abort() }, [parkId])
  useEffect(() => {
    if (!initialDocumentId) return
    const controller = new AbortController()
    setError(null)
    void api.document(initialDocumentId, controller.signal).then(next => {
      if (!controller.signal.aborted) setSelected(next)
    }).catch(caught => { if (!controller.signal.aborted) setError(errorText(caught)) })
    return () => controller.abort()
  }, [api, initialDocumentId])
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const data = new FormData(event.currentTarget); setError(null)
    try {
      const title = String(data.get('title') || '').trim(), content = String(data.get('content') || '').trim(), kind = String(data.get('kind')) as KnowledgeKind, nextState = String(data.get('state')) as KnowledgeState
      const saved = selected ? await api.updateDocument(selected.id, { revision: selected.revision, title, content, state: nextState }) : await api.createDocument({ title, content, kind, park_id: parkId, state: nextState })
      setSelected(saved); setEditing(false); setMessage('Документ сохранён.'); void documents.refresh()
    } catch (caught) { setError(errorText(caught)) }
  }
  const remove = async () => { if (!selected) return; await api.deleteDocument(selected.id); setSelected(null); setEditing(false); void documents.refresh() }
  return <div className="rp-assistant-knowledge">
    <Panel title="Поиск" actions={canManage ? <Button onClick={() => { setSelected(null); setEditing(true) }} size="compact">Добавить</Button> : null}>
      <KnowledgeBundleNotice bundle={bundle} />
      <div className="rp-assistant-filters"><FormField id="knowledge-query" label="Текст"><input onChange={event => setQuery(event.target.value)} placeholder="Например, лидар" value={query} /></FormField><FormField id="knowledge-state" label="Статус"><select onChange={event => setState(event.target.value as KnowledgeState | '')} value={state}><option value="">Все</option><option value="active">Действующие</option><option value="candidate">Кандидаты</option><option value="rejected">Отклонённые</option></select></FormField></div>
      {documents.loading ? <LoadingState label="Ищем документы" /> : documents.error ? <ErrorState title="Поиск не выполнен" description={documents.error} onRetry={() => void documents.refresh()} /> : documents.data?.items.length ? <ul className="rp-assistant-list">{documents.data.items.map(item => <li key={item.id}><button onClick={() => void open(item)} type="button"><strong>{item.title}</strong><span>{item.kind} · {item.state} · {item.trust}</span></button></li>)}</ul> : <EmptyState title="Ничего не найдено" description="Измените запрос или добавьте документ." />}
      {canManage ? <KnowledgeImport api={api} parkId={parkId} onDone={() => void documents.refresh()} /> : null}
    </Panel>
    <Panel title={editing ? (selected ? 'Редактирование' : 'Новый документ') : selected?.title ?? 'Документ'}>
      {message ? <Alert tone="success">{message}</Alert> : null}{error ? <Alert tone="error">{error}</Alert> : null}
      {editing ? <form className="rp-assistant-form" onSubmit={save}><FormField id="knowledge-title" label="Название" required><input defaultValue={selected?.title} name="title" /></FormField><FormField id="knowledge-kind" label="Тип"><select defaultValue={selected?.kind ?? 'manual'} disabled={Boolean(selected)} name="kind"><option value="manual">Инструкция</option><option value="note">Заметка</option><option value="ticket">Тикет</option><option value="chat">Чат</option></select></FormField><FormField id="knowledge-edit-state" label="Статус"><select defaultValue={selected?.state ?? 'candidate'} name="state"><option value="active">Действующий</option><option value="candidate">Кандидат</option><option value="rejected">Отклонён</option></select></FormField><FormField id="knowledge-content" label="Содержание" required><textarea defaultValue={selected?.content} name="content" rows={14} /></FormField><div className="rp-assistant-actions"><Button type="submit">Сохранить</Button><Button onClick={() => setEditing(false)} type="button" variant="secondary">Отмена</Button>{selected ? <Button onClick={() => void remove()} type="button" variant="danger">Удалить</Button> : null}</div></form> : selected ? <><p className="rp-assistant-document-meta">{selected.kind} · {selected.state} · {selected.trust}</p><div className="rp-assistant-document-content">{selected.content}</div>{canManage ? <Button onClick={() => setEditing(true)} variant="secondary">Редактировать</Button> : null}</> : <EmptyState title="Выберите документ" description="Откройте результат поиска слева." />}
    </Panel>
  </div>
}

function KnowledgeImport({ api, parkId, onDone }: { api: AssistantApiClient; parkId: number | null; onDone: () => void }) {
  const [parsed, setParsed] = useState<KnowledgeImportDocument[]>([]); const [localRejected, setLocalRejected] = useState(0)
  const [activate, setActivate] = useState(false); const [activateUnverified, setActivateUnverified] = useState(false); const [progress, setProgress] = useState(''); const [error, setError] = useState<string | null>(null); const [uploading, setUploading] = useState(false); const uploadingRef = useRef(false)
  const choose = async (file?: File) => { if (!file || uploadingRef.current) return; setError(null); try { const result = await parseKnowledgeFile(file); setParsed(result.documents); setLocalRejected(result.rejected); setProgress(`Готово к импорту: ${result.documents.length}; пропущено: ${result.rejected}`) } catch (caught) { setError(errorText(caught)); setParsed([]) } }
  const upload = async () => {
    if (uploadingRef.current || !parsed.length) return
    uploadingRef.current = true; setUploading(true); setError(null); let created = 0, duplicates = 0, rejected = localRejected, processed = 0
    try { for (const batch of createKnowledgeImportBatches(parsed, parkId, activate, activateUnverified)) { setProgress(`Импорт ${Math.min(processed + batch.length, parsed.length)} из ${parsed.length}…`); const result = await api.importDocuments(batch, parkId, activate, activateUnverified); processed += batch.length; created += result.created; duplicates += result.duplicates; rejected += result.rejected } setProgress(`Добавлено: ${created}; дубликатов: ${duplicates}; отклонено: ${rejected}`); onDone() } catch (caught) { setError(`Импорт остановлен. Уже обработанные пакеты сохранены. ${errorText(caught)}`) } finally { uploadingRef.current = false; setUploading(false) }
  }
  return <details className="rp-assistant-advanced"><summary>Пакетный импорт JSON/JSONL</summary><p>JSON или JSONL до 64 МиБ. Повторный импорт пропускает уже добавленные материалы.</p><input accept=".json,.jsonl,application/json,application/x-ndjson" aria-label="Файл базы знаний" disabled={uploading} onChange={event => void choose(event.target.files?.[0])} type="file" /><label><input aria-label="Сразу активировать инструкции" checked={activate} disabled={uploading} onChange={event => setActivate(event.target.checked)} type="checkbox" /> Сразу активировать инструкции</label><label><input checked={activateUnverified} disabled={uploading} onChange={event => setActivateUnverified(event.target.checked)} type="checkbox" /> Использовать тикеты, переписку и заметки как непроверенный опыт</label>{progress ? <p role="status">{progress}</p> : null}{error ? <Alert tone="error">{error}</Alert> : null}<Button busy={uploading} disabled={uploading || !parsed.length} onClick={() => void upload()}>Импортировать</Button></details>
}

function DraftGenerator({ api, kind, parkId, onProposal }: { api: AssistantApiClient; kind: 'script' | 'automation'; parkId: number | null; onProposal: (result: Record<string, unknown>) => void }) {
  const [instruction, setInstruction] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [proposal, setProposal] = useState<Record<string, unknown> | null>(null); const [copied, setCopied] = useState(false)
  const pollController = useRef<AbortController | null>(null)
  useEffect(() => () => pollController.current?.abort(), [])
  const generate = async () => { if (parkId == null || !instruction.trim()) return; const controller = new AbortController(); pollController.current?.abort(); pollController.current = controller; setBusy(true); setError(null); setCopied(false); try { const { job: complete, timedOut } = await waitForJob(api, await api.createDraft({ kind, instruction: instruction.trim(), park_id: parkId }), controller.signal); if (controller.signal.aborted) return; if (timedOut || complete.state !== 'succeeded' || !complete.result || typeof complete.result !== 'object') throw new Error(complete.error || (timedOut ? 'Черновик ещё готовится. Попробуйте открыть раздел позже.' : 'Черновик не создан')); const next = complete.result as Record<string, unknown>; setProposal(next); onProposal(next) } catch (caught) { if (!controller.signal.aborted) setError(errorText(caught)) } finally { if (!controller.signal.aborted) setBusy(false); if (pollController.current === controller) pollController.current = null } }
  const copy = async () => { if (!proposal) return; try { await navigator.clipboard.writeText(JSON.stringify(proposal, null, 2)); setCopied(true) } catch { setError('Не удалось скопировать предложение. Выделите текст вручную.') } }
  return <details className="rp-assistant-advanced"><summary>Создать черновик с помощником</summary><FormField id={`draft-${kind}`} label="Что нужно сделать"><textarea onChange={event => setInstruction(event.target.value)} rows={3} value={instruction} /></FormField>{error ? <Alert tone="error">{error}</Alert> : null}<Button busy={busy} disabled={parkId == null || !instruction.trim()} onClick={() => void generate()}>Подготовить предложение</Button>{proposal ? <><pre>{JSON.stringify(proposal, null, 2)}</pre><Button onClick={() => void copy()} variant="secondary">Копировать предложение</Button>{copied ? <span role="status">Скопировано.</span> : null}</> : null}<p>Предложение перенесено в редактор, но не сохраняется и не включается автоматически.</p></details>
}

function ScriptsPanel({ api, parkId }: { api: AssistantApiClient; parkId: number | null }) {
  const resource = useLoader(signal => api.scripts(signal), [api]); const [selected, setSelected] = useState<AssistantScript | null>(null); const [name, setName] = useState(''); const [source, setSource] = useState('def main(data):\n    return {}'); const [input, setInput] = useState('{}'); const [result, setResult] = useState(''); const [error, setError] = useState<string | null>(null)
  const testPollController = useRef<AbortController | null>(null)
  useEffect(() => () => testPollController.current?.abort(), [])
  const select = (item: AssistantScript) => { setSelected(item); setName(item.name); setSource(item.source); setResult('') }
  const save = async () => { setError(null); try { const next = selected ? await api.updateScript(selected.id, { name, source, revision: selected.revision }) : await api.createScript({ name, source }); setSelected(next); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const test = async () => { if (!selected) return; const controller = new AbortController(); testPollController.current?.abort(); testPollController.current = controller; setError(null); try { const parsed = JSON.parse(input) as unknown; const { job: complete, timedOut } = await waitForJob(api, await api.testScript(selected.id, parsed), controller.signal); if (controller.signal.aborted) return; setResult(JSON.stringify(complete.result, null, 2)); if (timedOut || complete.state !== 'succeeded') throw new Error(complete.error || (timedOut ? 'Проверка ещё выполняется' : 'Проверка не пройдена')); const all = await resource.refresh(); const current = all?.find(item => item.id === selected.id); if (current) select(current) } catch (caught) { if (!controller.signal.aborted) setError(errorText(caught)) } finally { if (testPollController.current === controller) testPollController.current = null } }
  const toggle = async () => { if (!selected) return; try { const next = await api.updateScript(selected.id, { revision: selected.revision, enabled: !selected.enabled }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Скрипты" actions={<Button onClick={() => { setSelected(null); setName(''); setSource('def main(data):\n    return {}') }} size="compact">Новый</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => select(item)} type="button"><strong>{item.name}</strong><span>r{item.revision} · {item.enabled ? 'включён' : 'выключен'} · тест r{item.tested_revision ?? '—'}</span></button></li>)}</ul> : <EmptyState title="Скриптов пока нет" />}<DraftGenerator api={api} kind="script" parkId={parkId} onProposal={proposal => { setSelected(null); setName('Черновик'); setSource(String(proposal.source ?? '')) }} /></Panel><Panel title={selected ? selected.name : 'Редактор скрипта'}>{error ? <Alert tone="error">{error}</Alert> : null}<FormField id="script-name" label="Название"><input onChange={event => setName(event.target.value)} value={name} /></FormField><FormField id="script-source" label="Python: main(data) → JSON"><textarea className="rp-assistant-code" onChange={event => setSource(event.target.value)} rows={16} value={source} /></FormField><div className="rp-assistant-actions"><Button disabled={!name.trim() || !source.trim()} onClick={() => void save()}>Сохранить</Button>{selected ? <><Button onClick={() => void toggle()} variant="secondary">{selected.enabled ? 'Выключить' : 'Включить'}</Button><Button onClick={() => { if (window.confirm(`Удалить скрипт «${selected.name}»?`)) void api.deleteScript(selected.id).then(() => { setSelected(null); void resource.refresh() }) }} variant="danger">Удалить</Button></> : null}</div>{selected ? <details className="rp-assistant-advanced" open><summary>Проверка текущей ревизии</summary><FormField id="script-input" label="Входной JSON"><textarea className="rp-assistant-code" onChange={event => setInput(event.target.value)} rows={5} value={input} /></FormField><Button onClick={() => void test()}>Запустить тест</Button>{result ? <pre>{result}</pre> : null}</details> : null}</Panel></div>
}

function ConnectorsPanel({ api }: { api: AssistantApiClient }) {
  const resource = useLoader(signal => api.connectors(signal), [api]); const [selected, setSelected] = useState<Connector | null>(null); const [error, setError] = useState<string | null>(null)
  const edit = (item: Connector | null) => setSelected(item)
  const save = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const value = { name: String(data.get('name')), url: String(data.get('url')), method: String(data.get('method')) as Connector['method'], token: String(data.get('token') || '') || undefined, enabled: data.get('enabled') === 'on' }; setError(null); try { const next = selected ? await api.updateConnector(selected.id, { ...value, revision: selected.revision }) : await api.createConnector(value); setSelected(next); await resource.refresh(); form.reset() } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Подключения" actions={<Button onClick={() => edit(null)} size="compact">Новое</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => edit(item)} type="button"><strong>{item.name}</strong><span>{item.method} · {item.enabled ? 'включено' : 'выключено'} · токен {item.token_set ? 'задан' : 'не задан'}</span></button></li>)}</ul> : <EmptyState title="Подключений пока нет" />}</Panel><Panel title={selected ? `Подключение: ${selected.name}` : 'Новое подключение'}><p>Адрес фиксируется администратором. Секрет после сохранения не отображается.</p>{error ? <Alert tone="error">{error}</Alert> : null}<form className="rp-assistant-form" key={selected?.id ?? 'new'} onSubmit={save}><FormField id="connector-name" label="Название" required><input defaultValue={selected?.name} name="name" /></FormField><FormField id="connector-url" label="HTTPS адрес" required><input defaultValue={selected?.url} inputMode="url" name="url" placeholder="https://service.example/issues/{{issue_key}}" type="text" /></FormField><p className="rp-assistant-muted">Шаблон {'{{issue_key}}'} разрешён только в пути, например https://service.example/issues/{'{{issue_key}}'}.</p><FormField id="connector-method" label="Метод"><select defaultValue={selected?.method ?? 'POST'} name="method"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option></select></FormField><FormField id="connector-token" label={selected?.token_set ? 'Новый bearer-токен (необязательно)' : 'Bearer-токен'}><input autoComplete="new-password" name="token" type="password" /></FormField><label><input defaultChecked={selected?.enabled} name="enabled" type="checkbox" /> Включено</label><div className="rp-assistant-actions"><Button type="submit">Сохранить</Button>{selected ? <Button onClick={() => { if (window.confirm(`Удалить подключение «${selected.name}»?`)) void api.deleteConnector(selected.id).then(() => { setSelected(null); void resource.refresh() }) }} type="button" variant="danger">Удалить</Button> : null}</div></form></Panel></div>
}

function AutomationsPanel({ api, parkId }: { api: AssistantApiClient; parkId: number | null }) {
  const resource = useLoader(signal => api.automations(signal), [api]); const [selected, setSelected] = useState<Automation | null>(null); const [name, setName] = useState(''); const [filters, setFilters] = useState('{"component_ids":[],"defect_codes":[],"keywords":[]}'); const [action, setAction] = useState('{"body":{}}'); const [event, setEvent] = useState('{}'); const [output, setOutput] = useState(''); const [error, setError] = useState<string | null>(null)
  const select = (item: Automation) => { setSelected(item); setName(item.name); setFilters(JSON.stringify(item.filters, null, 2)); setAction(JSON.stringify(item.action, null, 2)) }
  const save = async () => { if (parkId == null) return; setError(null); try { const parsedFilters = JSON.parse(filters) as Automation['filters'], parsedAction = JSON.parse(action) as Automation['action']; const next = selected ? await api.updateAutomation(selected.id, { revision: selected.revision, name, filters: parsedFilters, action: parsedAction }) : await api.createAutomation({ name, park_id: parkId, filters: parsedFilters, action: parsedAction }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const toggle = async () => { if (!selected) return; try { const next = await api.updateAutomation(selected.id, { revision: selected.revision, enabled: !selected.enabled }); select(next); await resource.refresh() } catch (caught) { setError(errorText(caught)) } }
  const preview = async () => { if (!selected) return; try { const result = await api.previewAutomation(selected.id, JSON.parse(event) as unknown); setOutput(JSON.stringify(result, null, 2)) } catch (caught) { setError(errorText(caught)) } }
  return <div className="rp-assistant-admin-layout"><Panel title="Правила" actions={<Button onClick={() => { setSelected(null); setName(''); setFilters('{"component_ids":[],"defect_codes":[],"keywords":[]}'); setAction('{"body":{}}') }} size="compact">Новое</Button>}>{resource.data?.length ? <ul className="rp-assistant-list">{resource.data.map(item => <li key={item.id}><button onClick={() => select(item)} type="button"><strong>{item.name}</strong><span>{item.enabled ? 'включено' : 'выключено'} · r{item.revision}</span></button></li>)}</ul> : <EmptyState title="Автоматизаций пока нет" />}<DraftGenerator api={api} kind="automation" parkId={parkId} onProposal={proposal => { setSelected(null); setName('Черновик'); setFilters(JSON.stringify((proposal.proposal as Record<string, unknown>)?.filters ?? {}, null, 2)); setAction(JSON.stringify((proposal.proposal as Record<string, unknown>)?.action ?? {}, null, 2)); setOutput(String(proposal.explanation ?? '')) }} /></Panel><Panel title={selected ? selected.name : 'Редактор правила'}>{error ? <Alert tone="error">{error}</Alert> : null}<FormField id="automation-name" label="Название"><input onChange={e => setName(e.target.value)} value={name} /></FormField><details className="rp-assistant-advanced" open><summary>Фильтры и действие</summary><FormField id="automation-filters" label="Фильтры JSON"><textarea className="rp-assistant-code" onChange={e => setFilters(e.target.value)} rows={7} value={filters} /></FormField><FormField id="automation-action" label="Действие JSON"><textarea className="rp-assistant-code" onChange={e => setAction(e.target.value)} rows={7} value={action} /></FormField></details><div className="rp-assistant-actions"><Button disabled={!name.trim() || parkId == null} onClick={() => void save()}>Сохранить выключенным</Button>{selected ? <><Button onClick={() => void toggle()} variant="secondary">{selected.enabled ? 'Выключить' : 'Включить'}</Button><Button onClick={() => void api.deleteAutomation(selected.id).then(() => { setSelected(null); void resource.refresh() })} variant="danger">Удалить</Button></> : null}</div>{selected ? <details className="rp-assistant-advanced"><summary>Пробный прогон без внешнего запроса</summary><FormField id="automation-event" label="Событие JSON"><textarea className="rp-assistant-code" onChange={e => setEvent(e.target.value)} rows={6} value={event} /></FormField><Button onClick={() => void preview()}>Проверить</Button>{output ? <pre>{output}</pre> : null}</details> : output ? <pre>{output}</pre> : null}</Panel></div>
}

function SettingsPanel({ api, status, onStatus }: { api: AssistantApiClient; status: AiStatus; onStatus: (status: AiStatus) => void }) {
  const config = useLoader(signal => api.config(signal), [api]); const prompts = useLoader(signal => api.prompts(signal), [api]); const runs = useLoader(signal => api.runs(50, signal), [api]); const [error, setError] = useState<string | null>(null); const [message, setMessage] = useState<string | null>(null); const [runtimeBusy, setRuntimeBusy] = useState<null | 'install' | 'enable' | 'disable' | 'remove_model'>(null); const runtimeBusyRef = useRef(false); const [cleanupBusy, setCleanupBusy] = useState<string | null>(null); const cleanupBusyRef = useRef(false)
  const updateConfig = async (patch: Partial<AiConfig>) => { if (!config.data) return; try { config.setData(await api.updateConfig({ ...config.data, ...patch })) } catch (caught) { setError(errorText(caught)) } }
  const runtime = async (action: 'install' | 'enable' | 'disable' | 'remove_model') => { if (runtimeBusyRef.current || (action === 'remove_model' && !window.confirm('Удалить локальную модель? Для повторного запуска потребуется новая установка.'))) return; runtimeBusyRef.current = true; setRuntimeBusy(action); setError(null); try { onStatus(await api.runtime(action)) } catch (caught) { setError(errorText(caught)) } finally { runtimeBusyRef.current = false; setRuntimeBusy(null) } }
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
  return <div className="rp-assistant-settings">{error ? <Alert tone="error">{error}</Alert> : null}{message ? <Alert tone="success">{message}</Alert> : null}<Panel title="Модель и обучение"><dl className="rp-assistant-status"><div><dt>Модель</dt><dd>{status.model}</dd></div><div><dt>Установка</dt><dd>{status.installed ? 'установлена' : 'не установлена'}</dd></div><div><dt>Исполнение</dt><dd>{status.ready ? 'готово' : statusReasonText(status.reason)}</dd></div><div><dt>Backend</dt><dd>{status.backend ?? '—'}</dd></div></dl>{config.data ? <><label><input checked={config.data.enabled} onChange={e => void updateConfig({ enabled: e.target.checked })} type="checkbox" /> Помощник включён</label><label><input checked={config.data.learning_enabled} onChange={e => void updateConfig({ learning_enabled: e.target.checked })} type="checkbox" /> Сохранять подтверждённый опыт ремонта</label></> : null}<details className="rp-assistant-advanced"><summary>Управление runtime</summary><div className="rp-assistant-actions"><Button busy={runtimeBusy === 'install'} disabled={runtimeDisabled} onClick={() => void runtime('install')} variant="secondary">Установить</Button><Button busy={runtimeBusy === 'enable'} disabled={runtimeDisabled} onClick={() => void runtime('enable')} variant="secondary">Включить</Button><Button busy={runtimeBusy === 'disable'} disabled={runtimeDisabled} onClick={() => void runtime('disable')} variant="secondary">Выключить</Button><Button busy={runtimeBusy === 'remove_model'} disabled={runtimeDisabled} onClick={() => void runtime('remove_model')} variant="danger">Удалить модель</Button></div></details></Panel><Panel title="Промпты ролей">{prompts.data?.map(prompt => <PromptEditor api={api} key={prompt.role} prompt={prompt} onSaved={next => prompts.setData((prompts.data ?? []).map(item => item.role === next.role ? next : item))} />)}</Panel><Panel title="Журнал автоматизаций" actions={<Button onClick={() => void runs.refresh()} size="compact" variant="secondary">Обновить</Button>}>{runs.data?.length ? <RunTable runs={runs.data} /> : <EmptyState title="Запусков пока нет" />}<details className="rp-assistant-advanced"><summary>Очистка журналов</summary><div className="rp-assistant-actions"><Button busy={cleanupBusy === 'runs'} disabled={cleanupBusy !== null} onClick={() => void cleanup('runs')} variant="secondary">Очистить запуски старше 30 дней</Button><Button busy={cleanupBusy === 'failed_jobs'} disabled={cleanupBusy !== null} onClick={() => void cleanup('failed_jobs')} variant="secondary">Очистить сбойные задания старше 30 дней</Button><Button busy={cleanupBusy === 'history'} disabled={cleanupBusy !== null} onClick={() => void cleanup('history')} variant="secondary">Очистить историю старше 30 дней</Button></div></details></Panel></div>
}

function PromptEditor({ api, prompt, onSaved }: { api: AssistantApiClient; prompt: AiPrompt; onSaved: (prompt: AiPrompt) => void }) {
  const [content, setContent] = useState(prompt.content); const [error, setError] = useState<string | null>(null)
  return <details className="rp-assistant-advanced"><summary>{prompt.role} · ревизия {prompt.revision}</summary><FormField id={`prompt-${prompt.role}`} label="Инструкции роли"><textarea onChange={e => setContent(e.target.value)} rows={8} value={content} /></FormField>{error ? <Alert tone="error">{error}</Alert> : null}<Button disabled={content === prompt.content} onClick={() => void api.updatePrompt(prompt.role, content, prompt.revision).then(onSaved).catch(caught => setError(errorText(caught)))}>Сохранить промпт</Button><p>Системная политика безопасности остаётся неизменной.</p></details>
}

function RunTable({ runs }: { runs: AutomationRun[] }) {
  return <div className="rp-assistant-table" role="table" aria-label="Запуски автоматизаций">{runs.map(run => <div key={run.id} role="row"><span role="cell">{run.event_key}</span><span role="cell">{run.state}</span><span role="cell">{run.error || new Date(run.created_at).toLocaleString('ru-RU')}</span></div>)}</div>
}
