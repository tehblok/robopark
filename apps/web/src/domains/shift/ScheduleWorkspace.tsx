import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, type ScheduleCopyCreate, type ScheduleCreate, type ScheduleDeleteOptions, type ScheduleEntry, type ScheduleListParams, type ScheduleParticipant, type SchedulePatternCreate, type ScheduleUpdate, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { TabPanel, Tabs } from '../../design-system/navigation/Tabs'
import { NotificationCenter } from '../../pwa/NotificationCenter'
import { resourceStore } from '../../lib/resource'
import { offlineScopeForUser } from '../../lib/deviceResourceCache'
import { useOptionalSync } from '../../pwa/syncContext'
import type { OfflineAction } from '../../pwa/offlineTypes'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { visibleRange } from './scheduleCalendar'
import { ScheduleCalendar, ScheduleViewControls, type ScheduleView } from './PersonalScheduleCalendar'
import { SchedulePlanner } from './SchedulePlanner'
import { ScheduleTeamGrid } from './ScheduleTeamGrid'
import { hasPendingScheduleCreate, projectScheduleActions } from './scheduleOffline'
import './ScheduleWorkspace.css'

export type ScheduleApiClient = {
  schedules: (params: ScheduleListParams) => Promise<ScheduleEntry[]>
  scheduleCreate: (payload: ScheduleCreate) => Promise<ScheduleEntry>
  scheduleUpdate: (id: string, payload: ScheduleUpdate) => Promise<ScheduleEntry>
  scheduleDelete: (id: string, options: ScheduleDeleteOptions) => Promise<void>
  scheduleBulk?: (payload: ScheduleCreate & { owner_user_ids: number[]; repeat_count: number; repeat_every_days: number }) => Promise<ScheduleEntry[]>
  schedulePattern: (payload: SchedulePatternCreate) => Promise<ScheduleEntry[]>
  scheduleCopy: (payload: ScheduleCopyCreate) => Promise<ScheduleEntry[]>
  scheduleParticipants: (parkId: number) => Promise<ScheduleParticipant[]>
}

const localIso = (value: string) => new Date(value).toISOString()
const localParts = new Intl.DateTimeFormat('sv-SE', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const toLocalInput = (value: string) => localParts.format(new Date(value)).replace(' ', 'T')
type ScheduleSection = 'mine' | 'team' | 'planning' | 'patterns'
const EMPTY_ACTIONS: OfflineAction[] = []

export function ScheduleWorkspace({ apiClient = api, initialAnchor, user, selectedParkId }: { apiClient?: ScheduleApiClient; initialAnchor?: Date; user: User; selectedParkId?: number | null }) {
  const sync = useOptionalSync()
  const [scheduleState, setScheduleState] = useState<{ key: string; items: ScheduleEntry[] } | null>(null)
  const [offlineState, setOfflineState] = useState<{ key: string; actions: OfflineAction[] } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [scheduleReload, setScheduleReload] = useState(0)
  const [editor, setEditor] = useState(false)
  const [kind, setKind] = useState<ScheduleCreate['kind']>('shift')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [participantState, setParticipantState] = useState<{ key: string; items: ScheduleParticipant[] } | null>(null)
  const [participantErrorKey, setParticipantErrorKey] = useState<string | null>(null)
  const [participantReload, setParticipantReload] = useState(0)
  const [editing, setEditing] = useState<ScheduleEntry | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [saveBusy, setSaveBusy] = useState(false)
  const savePending = useRef(false)
  const createAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const updateAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const deleteAttempts = useRef(new Map<string, ScheduleDeleteOptions>())
  const deletePending = useRef(new Set<string>())
  const [view, setView] = useState<ScheduleView>('week')
  const [anchor, setAnchor] = useState(() => initialAnchor ?? new Date())
  const [section, setSection] = useState<ScheduleSection>(() => user.role === 'admin' || user.role === 'royal' ? 'team' : 'mine')
  const range = useMemo(() => visibleRange(anchor, view), [anchor, view])
  const scheduleGeneration = useRef(0)
  const employeeGeneration = useRef(0)
  const actionGeneration = useRef(0)
  const observedPlannerConfirmations = useRef(new Set<string>())
  const parkId = selectedParkId
  const ownerUserId = ['admin', 'royal'].includes(user.role) ? undefined : user.id
  const permissionsKey = [...(user.permissions ?? [])].sort().join('\u0000')
  const scopeKey = parkId == null ? null : JSON.stringify([user.id, user.username, user.access_status, user.role, permissionsKey, parkId])
  const offlineScopeKey = parkId == null ? null : JSON.stringify(offlineScopeForUser(user, String(parkId)))
  const scopeEngineReady = sync !== null && sync.actionTrackingReady === true && sync.scopeKey === offlineScopeKey && typeof sync.listActions === 'function'
  const queueReady = scopeEngineReady && offlineState?.key === offlineScopeKey
  const requestKey = scopeKey == null ? null : JSON.stringify([scopeKey, range.start.toISOString(), range.end.toISOString()])
  const cacheKey = requestKey == null ? null : `schedule:${requestKey}`
  const baseItems = requestKey != null && scheduleState?.key === requestKey ? scheduleState.items : null
  const actions = offlineScopeKey && offlineState?.key === offlineScopeKey ? offlineState.actions : EMPTY_ACTIONS
  const projected = useMemo(() => projectScheduleActions(baseItems ?? [], actions, parkId ?? -1, user.id, range), [actions, baseItems, parkId, range, user.id])
  const items = baseItems !== null ? projected.items : (projected.items.length ? projected.items : null)
  const employees = scopeKey != null && participantState?.key === scopeKey ? participantState.items : []
  useEffect(() => {
    setEditor(false)
    setEditing(null)
    setActionError(null)
    createAttempt.current = null
    updateAttempt.current = null
    deleteAttempts.current.clear()
    observedPlannerConfirmations.current.clear()
  }, [scopeKey])
  useEffect(() => {
    const generation = ++actionGeneration.current
    if (!scopeEngineReady || !offlineScopeKey || !sync?.listActions) return
    void sync.listActions().then(value => {
      if (generation === actionGeneration.current) setOfflineState({ key: offlineScopeKey, actions: value })
    }).catch(() => {
      if (generation === actionGeneration.current) setActionError('Не удалось прочитать сохранённые изменения графика.')
    })
    return () => { actionGeneration.current += 1 }
  }, [offlineScopeKey, scopeEngineReady, sync, sync?.state])
  useEffect(() => {
    if (offlineState?.key !== offlineScopeKey) return
    const newlyConfirmed = offlineState.actions.filter(action =>
      ['schedule_pattern', 'schedule_copy'].includes(action.action)
      && action.state === 'confirmed' && !observedPlannerConfirmations.current.has(action.id))
    if (!newlyConfirmed.length) return
    newlyConfirmed.forEach(action => observedPlannerConfirmations.current.add(action.id))
    setScheduleReload(current => current + 1)
  }, [offlineScopeKey, offlineState])
  useEffect(() => {
    if (requestKey == null || cacheKey == null) return
    let active = true
    const cached = resourceStore.get<ScheduleEntry[]>(cacheKey)
    const applySnapshot = (snapshot: ScheduleEntry[]) => setScheduleState(current =>
      current?.key === requestKey ? current : { key: requestKey, items: snapshot })
    if (cached) applySnapshot(cached)
    else void resourceStore.hydrate<ScheduleEntry[]>(cacheKey).then(value => {
      if (active && value) applySnapshot(value)
    }).catch(() => undefined)
    return () => { active = false }
  }, [cacheKey, requestKey])
  useEffect(() => {
    if (cacheKey && scheduleState?.key === requestKey) resourceStore.set(cacheKey, scheduleState.items, false)
  }, [cacheKey, requestKey, scheduleState])
  useEffect(() => {
    if (parkId == null || requestKey == null || offlineState?.key !== offlineScopeKey) return
    const confirmed = offlineState.actions.filter(action => action.state === 'confirmed')
    if (!confirmed.length) return
    setScheduleState(current => {
      if (current?.key !== requestKey) return current
      const reconciled = projectScheduleActions(current.items, confirmed, parkId, user.id, range).items
      return JSON.stringify(reconciled) === JSON.stringify(current.items) ? current : { ...current, items: reconciled }
    })
  }, [offlineScopeKey, offlineState, parkId, range, requestKey, scheduleState, user.id])
  useEffect(() => {
    const generation = ++scheduleGeneration.current
    if (parkId == null || requestKey == null) return
    const controller = new AbortController()
    setErrorKey(null)
    setLoadError(null)
    void apiClient.schedules({ parkId, ownerUserId, startAt: range.start.toISOString(), endAt: range.end.toISOString(), signal: controller.signal }).then(value => {
      if (generation !== scheduleGeneration.current) return
      const next = { key: requestKey, items: value }
      setScheduleState(next)
    }).catch((reason) => {
      if (generation !== scheduleGeneration.current) return
      const failure = classifyApiError(reason, 'Не удалось загрузить график.')
      if (failure.kind === 'forbidden' || failure.kind === 'unauthorized') {
        setScheduleState(null)
        if (cacheKey) resourceStore.evict(cacheKey)
      }
      setLoadError(reason)
      setErrorKey(requestKey)
    })
    return () => { controller.abort(); scheduleGeneration.current += 1 }
  }, [apiClient, cacheKey, ownerUserId, parkId, range.end, range.start, requestKey, scheduleReload])
  useEffect(() => {
    const generation = ++employeeGeneration.current
    if (parkId == null || scopeKey == null) return
    setParticipantErrorKey(null)
    if (!['admin', 'royal'].includes(user.role)) {
      setParticipantState({ key: scopeKey, items: [] })
      return
    }
    void apiClient.scheduleParticipants(parkId).then(value => {
      if (generation !== employeeGeneration.current) return
      setParticipantState({ key: scopeKey, items: value })
      setParticipantErrorKey(null)
    }).catch(() => {
      if (generation === employeeGeneration.current) setParticipantErrorKey(scopeKey)
    })
    return () => { employeeGeneration.current += 1 }
  }, [apiClient, parkId, participantReload, scopeKey, user.role])
  if (parkId == null) return <PageLayout title="График"><EmptyState title="Выберите парк" /></PageLayout>
  const scheduleFailure = errorKey === requestKey ? classifyApiError(loadError, 'Не удалось загрузить график.') : null
  if (scheduleFailure && !items) return <PageLayout title="График"><ErrorState description={scheduleFailure.description} onRetry={scheduleFailure.retryable ? () => setScheduleReload(current => current + 1) : undefined} title="Не удалось загрузить график" /></PageLayout>
  if (!items) return <PageLayout title="График"><LoadingState label="Загружаем график" variant="page" /></PageLayout>
  const admin = user.role === 'admin'
  const royal = user.role === 'royal'
  const tabs = admin
    ? [{ id: 'team', label: 'Команда' }]
    : royal
      ? [{ id: 'mine', label: 'Мой календарь' }, { id: 'team', label: 'Команда' }, { id: 'planning', label: 'Планирование' }]
      : [{ id: 'mine', label: 'Мой календарь' }, { id: 'patterns', label: 'Шаблоны' }]
  const selectedSection = tabs.some(tab => tab.id === section) ? section : tabs[0].id as ScheduleSection
  const participantView = participantErrorKey === scopeKey
    ? <ErrorState description="Состав команды не получен. Повторите загрузку." onRetry={() => setParticipantReload(current => current + 1)} retryLabel="Повторить загрузку участников" title="Не удалось загрузить участников графика" />
    : participantState?.key !== scopeKey
      ? <LoadingState label="Загружаем участников графика" variant="inline" />
      : null
  const handleActionFailure = (reason: unknown, fallback: string) => {
    const failure = classifyApiError(reason, fallback)
    if (failure.kind === 'forbidden' || failure.kind === 'unauthorized') {
      setScheduleState(null)
      if (cacheKey) resourceStore.evict(cacheKey)
      setLoadError(reason)
      setErrorKey(requestKey)
      setEditor(false)
      setEditing(null)
      return
    }
    setActionError(failure.description)
  }
  const recordQueued = (action: OfflineAction) => {
    if (!offlineScopeKey) return
    setOfflineState(current => {
      const previous = current?.key === offlineScopeKey ? current.actions.filter(item => item.id !== action.id) : []
      return { key: offlineScopeKey, actions: [...previous, action] }
    })
    if (action.state === 'confirmed') {
      setScheduleState(current => current?.key === requestKey
        ? { ...current, items: projectScheduleActions(current.items, [action], parkId, user.id, range).items }
        : current)
    }
  }
  const queuePlanner = async (name: 'schedule_pattern' | 'schedule_copy', payload: Record<string, unknown>, key: string) => {
    if (!sync || !queueReady) throw new Error('schedule_queue_not_ready')
    if (actions.some(action => action.action === name && ['local', 'ready', 'sending'].includes(action.state)
      && JSON.stringify(action.payload) === JSON.stringify(payload))) throw new Error('schedule_already_pending')
    const queued = await sync.enqueueAction({
      id: key, deviceId: 'local', resourceType: 'schedule_entry', resourceId: key,
      action: name, idempotencyKey: key, baseRevision: null, dependencies: [], payload,
    }) as OfflineAction
    recordQueued(queued)
    if (queued.state === 'confirmed') {
      observedPlannerConfirmations.current.add(queued.id)
      setScheduleReload(current => current + 1)
    }
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!parkId || !startAt || !endAt || savePending.current) return
    savePending.current = true
    setSaveBusy(true)
    setActionError(null)
    if (sync && !queueReady) {
      setActionError('Фоновая обработка ещё не готова. Повторите сохранение через несколько секунд.')
      savePending.current = false
      setSaveBusy(false)
      return
    }
    const base = { park_id: parkId, kind, start_at: localIso(startAt), end_at: localIso(endAt), owner_user_id: undefined, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' }
    try {
      if (editing) {
        const fingerprint = JSON.stringify([editing.id, editing.updated_at, base])
        const key = updateAttempt.current?.fingerprint === fingerprint
          ? updateAttempt.current.key : globalThis.crypto.randomUUID()
        updateAttempt.current = { fingerprint, key }
        if (sync) {
          const queued = await sync.enqueueAction({
            id: key, deviceId: 'local', resourceType: 'schedule_entry', resourceId: editing.id,
            action: 'schedule_update', idempotencyKey: key, baseRevision: editing.updated_at,
            dependencies: [], payload: base,
          }) as OfflineAction
          recordQueued(queued)
        } else {
          const updated = await apiClient.scheduleUpdate(editing.id, {
            ...base, base_revision: editing.updated_at, idempotency_key: key,
          })
          setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.map(item => item.id === updated.id ? updated : item) } : current)
        }
        updateAttempt.current = null
      } else {
        if (sync && hasPendingScheduleCreate(actions, parkId, user.id, base)) {
          setActionError('Такой период уже сохранён на устройстве.')
          return
        }
        const fingerprint = JSON.stringify(base)
        const key = createAttempt.current?.fingerprint === fingerprint
          ? createAttempt.current.key : globalThis.crypto.randomUUID()
        createAttempt.current = { fingerprint, key }
        if (sync) {
          const queued = await sync.enqueueAction({
            id: key, deviceId: 'local', resourceType: 'schedule_entry', resourceId: key,
            action: 'schedule_create', idempotencyKey: key, baseRevision: null,
            dependencies: [], payload: { ...base, owner_user_id: user.id },
          }) as OfflineAction
          recordQueued(queued)
        } else {
          const created = await apiClient.scheduleCreate({ ...base, idempotency_key: key })
          setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, created] } : current)
        }
        createAttempt.current = null
      }
      setEditor(false); setEditing(null)
    } catch (reason) {
      handleActionFailure(reason, 'Не удалось сохранить период.')
    } finally {
      savePending.current = false
      setSaveBusy(false)
    }
  }
  const openCreate = () => { createAttempt.current = null; setActionError(null); setEditing(null); setKind('shift'); setStartAt(''); setEndAt(''); setEditor(true) }
  const openEdit = (item: ScheduleEntry) => { setActionError(null); setEditing(item); setKind(item.kind); setStartAt(toLocalInput(item.start_at)); setEndAt(toLocalInput(item.end_at)); setEditor(true) }
  const remove = async (item: ScheduleEntry) => {
    if (projected.pendingIds.has(item.id)) return
    if (sync && !queueReady) {
      setActionError('Фоновая обработка ещё не готова. Повторите удаление через несколько секунд.')
      return
    }
    if (deletePending.current.has(item.id)) return
    deletePending.current.add(item.id)
    setActionError(null)
    const previous = deleteAttempts.current.get(item.id)
    const options = previous?.base_revision === item.updated_at
      ? previous
      : { base_revision: item.updated_at, idempotency_key: globalThis.crypto.randomUUID() }
    deleteAttempts.current.set(item.id, options)
    try {
      if (sync) {
        const queued = await sync.enqueueAction({
          id: options.idempotency_key, deviceId: 'local', resourceType: 'schedule_entry', resourceId: item.id,
          action: 'schedule_delete', idempotencyKey: options.idempotency_key,
          baseRevision: item.updated_at, dependencies: [], payload: { park_id: parkId },
        }) as OfflineAction
        recordQueued(queued)
      } else await apiClient.scheduleDelete(item.id, options)
      deleteAttempts.current.delete(item.id)
      if (!sync) setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.filter(row => row.id !== item.id) } : current)
    } catch (reason) {
      handleActionFailure(reason, 'Не удалось удалить период.')
    } finally {
      deletePending.current.delete(item.id)
    }
  }
  const editorPanel = editor ? <Panel title={editing ? 'Изменить период' : 'Новый период'}><form className="rp-schedule__editor" onSubmit={submit}><label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleCreate['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label><label>Начало<input aria-label="Начало" onChange={event => setStartAt(event.target.value)} required type="datetime-local" value={startAt} /></label><label>Конец<input aria-label="Конец" onChange={event => setEndAt(event.target.value)} required type="datetime-local" value={endAt} /></label>{actionError ? <p role="alert">{actionError}</p> : null}<div><Button busy={saveBusy} disabled={sync !== null && !queueReady} type="submit">Сохранить</Button><Button onClick={() => setEditor(false)} type="button" variant="ghost">Отмена</Button></div></form></Panel> : null
  const addPlanned = (created: ScheduleEntry[]) => setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, ...created] } : current)
  const moveRange = (direction: -1 | 1) => setAnchor(direction < 0 ? new Date(range.start.getTime() - 12 * 60 * 60 * 1000) : range.end)
  return <PageLayout className="rp-schedule" title={admin || royal ? 'График команды' : 'Мой график'} description="Смены, отпуск и болезнь. Время указано по часовому поясу устройства." actions={<div className="rp-schedule__range"><Button aria-label="Предыдущий период" onClick={() => moveRange(-1)} size="compact" variant="secondary">Назад</Button><Button onClick={() => setAnchor(new Date())} size="compact" variant="ghost">Сегодня</Button><Button aria-label="Следующий период" onClick={() => moveRange(1)} size="compact" variant="secondary">Вперёд</Button></div>}>
    {scheduleFailure ? <ErrorState description={`Показаны последние сохранённые данные. ${scheduleFailure.description}`} onRetry={scheduleFailure.retryable ? () => setScheduleReload(current => current + 1) : undefined} title="Не удалось обновить график" /> : null}
    {baseItems === null && projected.items.length ? <p role="status">Показаны только изменения, сохранённые на этом устройстве. Полный график пока недоступен.</p> : null}
    {projected.attentionIds.size ? <p role="alert">Некоторые изменения графика не применены. Обновите график.</p> : null}
    {actionError && !editor ? <p role="alert">{actionError}</p> : null}
    <Tabs ariaLabel="Разделы графика" items={tabs} onChange={id => { setEditor(false); setSection(id as ScheduleSection) }} panelIdFor={id => `schedule-panel-${id}`} value={selectedSection} />
    {!admin ? <TabPanel active={selectedSection === 'mine'} id="schedule-panel-mine" labelledBy="tab-mine">
      {selectedSection === 'mine' ? <><Panel actions={<Button disabled={sync !== null && !queueReady} onClick={openCreate}>Добавить период</Button>} title="Мой календарь">
        <ScheduleCalendar days={range.days} items={items} onDelete={item => void remove(item)} onEdit={openEdit} onViewChange={setView} ownerUserId={user.id} pendingIds={projected.pendingIds} selectedDate={anchor} view={view} />
      </Panel>{editorPanel}</> : null}
    </TabPanel> : null}
    {admin || royal ? <TabPanel active={selectedSection === 'team'} id="schedule-panel-team" labelledBy="tab-team">
      {selectedSection === 'team' ? <><Panel actions={<ScheduleViewControls onViewChange={setView} view={view} />} title="Команда">
        {participantView ?? <ScheduleTeamGrid days={range.days} employees={employees} items={items} onDelete={royal ? item => void remove(item) : undefined} onEdit={royal ? openEdit : undefined} pendingIds={projected.pendingIds} selectedDate={anchor} />}
      </Panel>{editorPanel}</> : null}
    </TabPanel> : null}
    {royal ? <TabPanel active={selectedSection === 'planning'} id="schedule-panel-planning" labelledBy="tab-planning">
      {selectedSection === 'planning' ? <Panel description="Создайте смены по шаблону или скопируйте существующий период." title="Планирование">{participantView ?? <SchedulePlanner apiClient={apiClient} employees={employees} onCreated={addPlanned} onQueue={sync ? queuePlanner : undefined} parkId={parkId} queueReady={sync === null || queueReady} />}</Panel> : null}
    </TabPanel> : null}
    {!admin && !royal ? <TabPanel active={selectedSection === 'patterns'} id="schedule-panel-patterns" labelledBy="tab-patterns">
      {selectedSection === 'patterns' ? <Panel description="Повторяйте собственные смены, отпуск или болезнь без назначения другим сотрудникам." title="Мои шаблоны"><SchedulePlanner apiClient={apiClient} employees={[{ id: user.id, display_name: user.username, role: user.role === 'operator' ? 'operator' : user.role === 'driver' ? 'driver' : 'mechanic' }]} lockEmployees onCreated={addPlanned} onQueue={sync ? queuePlanner : undefined} parkId={parkId} queueReady={sync === null || queueReady} /></Panel> : null}
    </TabPanel> : null}
    <NotificationCenter />
  </PageLayout>
}

export function SchedulePage() {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  return user ? <ScheduleWorkspace selectedParkId={parkId} user={user} /> : null
}
