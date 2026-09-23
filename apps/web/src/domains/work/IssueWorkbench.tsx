import { TaskCollaboration } from '../../components/tracker/TaskCollaboration'
import { attachmentIdentity, runTrackerSubmission } from '../../components/tracker/trackerReliability'
import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  api,
  ApiError,
  type Park,
  type Paged,
  type TrackerIssue,
  type TaskTimelineItem,
  type TrackerIssueDetail,
  type User,
} from '../../api'
import { SyncStatus } from '../../design-system/status/SyncStatus'
import { IssueActionsPanel } from '../../components/tracker/IssueActionsPanel'
import { IssueDetailPanel } from '../../components/tracker/IssueDetailPanel'
import { IssueRichText } from '../../components/tracker/IssueRichText'
import { summarizeIssueDescription } from '../../components/tracker/issueDescription'
import { formatAge, personName, statusTone } from '../../components/tracker/issue-utils'
import { trackerStatusLabel } from '../../components/tracker/trackerStatusLabel'
import { Button } from '../../design-system/actions/Button'
import { EntityRow } from '../../design-system/data/EntityRow'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from '../../design-system/feedback/AsyncState'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { Panel } from '../../design-system/layout/PageLayout'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from '../../design-system/layout/ResponsiveDisclosure'
import { StatusBadge, type StatusTone } from '../../design-system/status/StatusBadge'
import {
  classifyApiError,
  type DomainError,
} from '../../shared/api/classifyApiError'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { TabPanel } from '../../design-system/navigation/Tabs'
import { useInterfaceMode } from '../../app/interface/InterfaceModeProvider'
import { TaskFirstWorkbench, type WorkSection } from './TaskFirstWorkbench'
import { TaskFirstTaskLayout } from './TaskFirstTaskLayout'
import { WorkRobotCheck } from './WorkRobotCheck'
import { TaskPartsPanel } from '../inventory/TaskPartsPanel'
import { WorkFilters } from './WorkFilters'
import { RepairSla } from './RepairSla'
import { SubmitReviewForm } from './SubmitReviewForm'
import { ReturnReviewForm } from './ReturnReviewForm'
import { TaskSyncStatus } from './TaskSyncStatus'
import { TaskTimeline } from './TaskTimeline'
import { StableMutationKey } from './stableMutationKey'
import { loadWorkPage, oldestFirst } from './workData'
import { buildCommentAction, buildHandoffAction, buildSubmitReviewAction } from './offlineTaskActions'
import type { OfflineAction } from '../../pwa/offlineTypes'
import { useOptionalSync } from '../../pwa/SyncProvider'
import { prepareImage } from '../../pwa/mediaPipeline'
import {
  buildWorkSearch,
  readWorkScroll,
  saveWorkScroll,
  workIssueHref,
  type WorkUrlState,
} from './workUrl'
import './work.css'

export type IssueWorkbenchApiClient = Pick<
  typeof api,
  | 'trackerIssues'
  | 'trackerIssue'
  | 'trackerComments'
  | 'trackerTransitions'
  | 'trackerComment'
  | 'trackerAttach'
  | 'trackerAssign'
  | 'trackerUnassign'
  | 'trackerTransition'
  | 'trackerClose'
  | 'inventory'
  | 'searchInventory'
  | 'writeoffInventoryForTask'
  | 'inventoryComponentPhotoUrl'
  | 'inventoryPartPhotoUrl'
> & Partial<Pick<typeof api,
  | 'taskTimeline' | 'taskDefectCodes' | 'taskMessage' | 'taskPhoto' | 'taskClaim' | 'taskHandoff'
  | 'taskSubmitReview' | 'taskReturnReview' | 'taskApproveReview'
  | 'taskRetryNow' | 'taskHide' | 'taskRestore'
>>

export type IssueWorkbenchProps = {
  apiClient?: IssueWorkbenchApiClient
  user: User
  selectedPark: Park
  issueKey?: string
  state: WorkUrlState
  onStateChange(
    next: WorkUrlState,
    options?: { replace?: boolean },
  ): void
  onOpenRelatedIssue?(key: string): void
  onOpenIssue(key: string): void
  onCloseIssue(): void
  onAuthorizationFailure(error: unknown): Promise<unknown>
  now?: number
}

const retainableFailureKinds = new Set<DomainError['kind']>([
  'offline',
  'timeout',
  'server',
])

function canRetainProtectedData(failure: DomainError | null): boolean {
  return failure != null && retainableFailureKinds.has(failure.kind)
}

function isAuthorizationFailure(failure: DomainError): boolean {
  return failure.kind === 'unauthorized' || failure.kind === 'forbidden'
}

function failureFor(error: unknown, fallback: string): DomainError | null {
  return error ? classifyApiError(error, fallback) : null
}

function mechanicOwnsIssue(user: User, issue: TrackerIssueDetail): boolean {
  const expected = user.username.trim().toLocaleLowerCase()
  const owner = issue.workflow ? issue.workflow.owner?.login : issue.assignee?.login
  return user.role === 'mechanic'
    && Boolean(expected)
    && owner?.trim().toLocaleLowerCase() === expected
}

function ClosedDisclosure({ title, children, open: controlledOpen, onOpenChange }: {
  title: string
  children: ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}) {
  const [localOpen, setLocalOpen] = useState(false)
  const open = controlledOpen ?? localOpen
  const toggle = () => {
    const next = !open
    if (controlledOpen === undefined) setLocalOpen(next)
    onOpenChange?.(next)
  }
  return <section className="rp-responsive-disclosure"><header className="rp-responsive-disclosure__header">
    <button aria-expanded={open} className="rp-responsive-disclosure__trigger" onClick={toggle} type="button">{title}</button>
  </header>{open ? <div className="rp-responsive-disclosure__content">{children}</div> : null}</section>
}

function TaskIssueSummary({ issue, now, onOpenRobotCheck, robotReadOnly }: { issue: TrackerIssueDetail; now: number; onOpenRobotCheck?: () => void; robotReadOnly: boolean }) {
  const robot = normalizedRobotNumber(issue.robot)
  const status = taskWorkflowStatus(issue.workflow?.display_status)
  const description = summarizeIssueDescription(issue.description ?? '')
  return <article className="issue-detail">
    <header className="issue-detail-head"><div className="issue-detail-title-row"><a className="issue-detail-key" href={issue.url} rel="noreferrer" target="_blank">{issue.key}</a><StatusBadge tone={status.tone}>{status.label}</StatusBadge></div><h2 className="issue-detail-summary">{issue.summary}</h2></header>
    <dl className="issue-fields">
      {robot ? <div className="issue-field"><dt>Робот</dt><dd>{robotReadOnly || !onOpenRobotCheck ? robot : <button className="rp-work-robot-link" onClick={onOpenRobotCheck} type="button">{robot}</button>}</dd></div> : null}
      <div className="issue-field"><dt>Ответственный</dt><dd>{personName(issue.workflow?.owner ?? issue.assignee)}</dd></div>
    </dl>
    <RepairSla deadline={issue.sla_deadline} now={now} source={issue.sla_source} />
    {description.trim() ? <section className="issue-section"><h3>Описание</h3><IssueRichText text={description} /></section> : null}
  </article>
}

function TaskRepairSequence({ canCheck, onCheck, onParts }: { canCheck: boolean; onCheck(): void; onParts(): void }) {
  const reveal = (id: string) => document.getElementById(id)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  return <section aria-label="Последовательность ремонта" className="a-task-sequence">
    <h3>Проверить робота → Запчасти → Что было сделано</h3>
    <ol>
      <li><strong>Проверить робота</strong>{canCheck ? <Button onClick={onCheck} variant="secondary">Открыть проверку</Button> : null}</li>
      <li><strong>Запчасти</strong><Button onClick={onParts} variant="secondary">Списать или заказать</Button></li>
      <li><strong>Что было сделано</strong><Button onClick={() => reveal('comment')} variant="secondary">Добавить комментарий</Button></li>
    </ol>
  </section>
}

function taskWorkflowStatus(value: string | undefined): { label: string; tone: StatusTone } {
  switch (value) {
    case 'queued': return { label: 'В очереди', tone: 'neutral' }
    case 'in_progress': return { label: 'В работе', tone: 'info' }
    case 'review': return { label: 'На проверке', tone: 'warning' }
    case 'closing': return { label: 'Закрытие не подтверждено', tone: 'warning' }
    case 'closed': return { label: 'Закрыта', tone: 'success' }
    case 'hidden': return { label: 'Скрыта', tone: 'neutral' }
    default: return { label: 'Статус обновляется', tone: 'neutral' }
  }
}

function EmbeddedTaskCollaboration(props: React.ComponentProps<typeof TaskCollaboration>) {
  const containerRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const details = containerRef.current?.querySelector('details')
    const summary = details?.querySelector('summary')
    if (!details || !summary) return
    details.open = true
    details.dispatchEvent(new Event('toggle', { bubbles: true }))
    summary.hidden = true
    summary.textContent = ''
  }, [])
  return <div ref={containerRef}><TaskCollaboration {...props} /></div>
}

const RELATED_PAGE_SIZE = 10
const FALLBACK_NOW = Date.now()

function normalizedRobotNumber(raw?: string | null): string | null {
  let text = raw?.trim().toUpperCase() ?? ''
  if (text.startsWith('[') && text.endsWith(']')) text = text.slice(1, -1)
  const digits = text.startsWith('YASADR') ? text.slice(6) : text.replace(/^A/, '')
  if (!/^\d+$/.test(digits)) return null
  return digits.replace(/^0+/, '') || '0'
}

function issueStatusTone(issue: TrackerIssue): StatusTone {
  switch (statusTone(issue)) {
    case 'done': return 'success'
    case 'waiting': return 'warning'
    case 'progress': return 'info'
    default: return 'neutral'
  }
}

function issueMeta(issue: TrackerIssue, now: number): ReactNode {
  const robot = normalizedRobotNumber(issue.robot)
  const age = formatAge(issue.hours_created) || 'неизвестен'
  return <div className="rp-work-issue-meta">
    <span className="rp-work-issue-age">Возраст: <strong>{age}</strong></span>
    <span>{[
      robot ? `Робот ${robot}` : 'Робот не указан',
      `Ответственный: ${personName(issue.assignee)}`,
    ].join(' · ')}</span>
    <RepairSla deadline={issue.sla_deadline} now={now} source={issue.sla_source} />
  </div>
}

function WorkIssueRows({
  items,
  selected,
  onOpen,
  user,
  apiClient,
  onClaimed,
  now,
}: {
  items: readonly TrackerIssue[]
  selected?: string
  onOpen: (key: string) => void
  user?: User
  apiClient?: IssueWorkbenchApiClient
  onClaimed?: () => void
  now: number
}) {
  const mechanicLogin = (user?.username || '').trim()
  const seen = new Set<string>()
  const uniqueItems = items.filter((item) => {
    if (seen.has(item.key)) return false
    seen.add(item.key)
    return true
  })
  return <div className="rp-work-entities">
    {uniqueItems.map((item) => <ClaimableIssueRow
      apiClient={apiClient} item={item} key={item.key} mechanicLogin={mechanicLogin}
      now={now} onClaimed={onClaimed} onOpen={onOpen} selected={selected}
      requireClaim={user?.role === 'mechanic'} />)}
  </div>
}

function ClaimableIssueRow({ item, selected, onOpen, requireClaim, mechanicLogin, apiClient, onClaimed, now }: {
  item: TrackerIssue; selected?: string; onOpen: (key: string) => void; requireClaim: boolean
  mechanicLogin: string; apiClient?: IssueWorkbenchApiClient; onClaimed?: () => void
  now: number
}) {
  const [claiming, setClaiming] = useState(false)
  const [claimError, setClaimError] = useState('')
  const mutationKey = useRef(new StableMutationKey())
  const assigned = item.assignee?.login?.trim() ?? ''
  const mine = Boolean(mechanicLogin && assigned.toLocaleLowerCase() === mechanicLogin.toLocaleLowerCase())
  const claim = async () => {
    if (!apiClient || !mechanicLogin || claiming) return
    setClaiming(true); setClaimError('')
    try {
      if (apiClient.taskClaim) {
        const payload = mechanicLogin
        const key = mutationKey.current.get('claim', payload)
        await apiClient.taskClaim(item.key, key)
        mutationKey.current.succeeded('claim', payload)
      }
      else await apiClient.trackerAssign(item.key, mechanicLogin)
      onClaimed?.()
      onOpen(item.key)
    } catch (error) {
      setClaimError(classifyApiError(error, 'Не удалось взять задачу в работу.').description)
    } finally { setClaiming(false) }
  }
  const openButton = <Button
    aria-current={item.key === selected ? 'page' : undefined}
    aria-label={`Открыть задачу ${item.key}: ${item.summary}`}
    onClick={() => onOpen(item.key)}
    variant="secondary"
  >Открыть</Button>
  const action = !requireClaim || mine
    ? openButton
    : assigned
      ? <>{openButton}<Button busy={claiming} disabled={!mechanicLogin} onClick={() => void claim()}>Взять вместо сменщика</Button></>
      : <Button busy={claiming} disabled={!mechanicLogin} onClick={() => void claim()}>Взять в работу</Button>
  return <EntityRow
    actions={<>{action}{claimError ? <span role="alert">{claimError}</span> : null}</>}
    meta={issueMeta(item, now)} status={<StatusBadge tone={issueStatusTone(item)}>{trackerStatusLabel(item.status, item.status_key)}</StatusBadge>}
    statusLabel={`Статус задачи ${item.key}`}
    title={<><strong>{item.key}</strong><span> · {item.summary}</span></>}
  />
}

function RelatedTaskGroup({
  title,
  empty,
  resource,
  onOpen,
  now,
}: {
  title: string
  empty: string
  resource: ReturnType<typeof useCachedResource<Paged<TrackerIssue>>>
  onOpen: (key: string) => void
  now: number
}) {
  const failure = failureFor(resource.error, `Не удалось загрузить раздел «${title}».`)
  const data = resource.data

  return <section aria-labelledby={`${title}-heading`} className="rp-work-related-tasks">
    <h3 id={`${title}-heading`}>{title}</h3>
    {failure ? <ErrorState
      description={failure.description}
      onRetry={failure.retryable ? () => void resource.refresh() : undefined}
      requestId={failure.requestId}
      title={failure.title}
    /> : resource.isLoading && !data ? <LoadingState label={`Загружаем: ${title.toLocaleLowerCase('ru')}`} />
      : data?.items.length ? <WorkIssueRows items={data.items} now={now} onOpen={onOpen} />
        : <p>{empty}</p>}
  </section>
}

function RelatedTasksPanel({ apiClient, issueKey, onOpen, park, resourcePrefix, robotNumber, queue, kind, now }: {
  apiClient: IssueWorkbenchApiClient; issueKey: string; onOpen: (key: string) => void
  park?: string; resourcePrefix: string; robotNumber: string; queue: string; kind: 'open' | 'closed'
  now: number
}) {
  const [page, setPage] = useState(0)
  const related = useCachedResource<Paged<TrackerIssue>>(
    `${resourcePrefix}:${kind}:${page}`,
    () => apiClient.trackerIssues({
      queue, park, robot_exact: robotNumber, exclude_key: issueKey, related_repairs: true,
      open_only: kind === 'open', ...(kind === 'closed' ? { status: 'closed' } : {}),
      sort: 'oldest', limit: RELATED_PAGE_SIZE, offset: page * RELATED_PAGE_SIZE,
    }),
  )
  return <>
    <p className="rp-work-list-count">Ремонты любого приоритета · от старых к новым{kind === 'closed' ? ' · закрыты за последние 14 дней' : ''}</p>
    <SyncStatus {...related} />
    <RelatedTaskGroup
      empty={kind === 'open' ? 'Открытых ремонтов по этому роботу нет.' : 'За последние 14 дней закрытых ремонтов по этому роботу нет.'}
      now={now} onOpen={onOpen} resource={related}
      title={`${kind === 'open' ? 'Открытые' : 'Закрытые'} задачи робота ${robotNumber}`}
    />
    {related.data ? <nav className="rp-work-pagination" aria-label="Страницы ремонтов">
      <Button variant="secondary" disabled={page === 0 || related.isRevalidating} onClick={() => setPage(value => value - 1)}>Назад</Button>
      <span>Страница {page + 1}</span>
      <Button variant="secondary" disabled={!related.data.has_more || related.isRevalidating} onClick={() => setPage(value => value + 1)}>Вперёд</Button>
    </nav> : null}
  </>
}

function workAccessKey(user: User, selectedPark: Park): string {
  const parkScope = (park: Park) => [park.id, park.tag?.trim(), park.tracker_queue?.trim(), park.is_active !== false]
  return JSON.stringify([
    user.id, user.username, user.tracker_login, user.role, user.access_status,
    Boolean(user.must_change_password), [...new Set(user.permissions ?? [])].sort(),
    [...user.parks].sort((a, b) => a.id - b.id).map(parkScope), parkScope(selectedPark),
  ])
}

function ResourceWarning({
  failure,
  onRetry,
}: {
  failure: DomainError
  onRetry: () => void
}) {
  return (
    <div className="rp-work-warning" role="alert">
      <StaleBadge state={failure.kind === 'offline' ? 'offline' : 'stale'} />
      <div className="rp-work-warning__copy">
        <strong>{failure.title}</strong>
        <span>{failure.description}</span>
        {failure.requestId ? <small>Код запроса: {failure.requestId}</small> : null}
      </div>
      {failure.retryable ? (
        <Button onClick={onRetry} size="compact" variant="secondary">
          Повторить
        </Button>
      ) : null}
    </div>
  )
}

function ResourceBoundary({
  failure,
  dataAvailable,
  onRetry,
  children,
}: {
  failure: DomainError | null
  dataAvailable: boolean
  onRetry: () => void
  children: ReactNode
}) {
  if (failure && (!dataAvailable || !canRetainProtectedData(failure))) {
    return (
      <ErrorState
        description={failure.description}
        onRetry={failure.retryable ? onRetry : undefined}
        requestId={failure.requestId}
        title={failure.title}
      />
    )
  }

  return (
    <>
      {failure ? <ResourceWarning failure={failure} onRetry={onRetry} /> : null}
      {children}
    </>
  )
}

export function TaskController({
  apiClient,
  user,
  selectedPark,
  issueKey,
  state,
  onStateChange,
  onOpenIssue,
  onOpenRelatedIssue,
  onCloseIssue,
  onAuthorizationFailure,
  accessKey,
  getAccessGeneration,
  now = FALLBACK_NOW,
}: Required<Pick<IssueWorkbenchProps, 'apiClient'>> & Omit<IssueWorkbenchProps, 'apiClient'> & {
  accessKey: string
  getAccessGeneration: () => number
}) {
  const sync = useOptionalSync()
  const cachePrefix = `work:${user.id}:`
  const { mode } = useInterfaceMode()
  const taskFirst = mode === 'task-first'
  const [taskFocus, setTaskFocus] = useState<'repair' | 'chat'>('repair')
  const accessPrefix = `${cachePrefix}${accessKey}:`
  const allowUntagged = user.role === 'operator'
    || user.role === 'admin'
    || user.role === 'royal'
  const manager = user.role === 'admin' || user.role === 'royal'
  const requestState = useMemo<WorkUrlState>(() => {
    if ((allowUntagged || !state.filters.untagged) && (manager || !state.filters.includeHidden)) return state
    const filters = { ...state.filters }
    if (!allowUntagged) delete filters.untagged
    if (!manager) delete filters.includeHidden
    return { ...state, filters }
  }, [allowUntagged, manager, state])
  const listKey = `${accessPrefix}list:${selectedPark.id}:${JSON.stringify({ filters: requestState.filters, sort: requestState.sort, page: requestState.page })}`
  const includeHidden = manager && Boolean(requestState.filters.includeHidden)
  const detailKey = issueKey ? `${accessPrefix}issue:${issueKey}${includeHidden ? ':hidden' : ''}` : ''
  const commentsKey = issueKey ? `${accessPrefix}comments:${issueKey}` : ''
  const ownedKey = `${accessPrefix}owned:${user.username}`
  const blockedRef = useRef(false)
  const blockedErrorRef = useRef<unknown>(null)
  const refreshStartedRef = useRef(false)
  const ownerGeneration = useRef(0)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const [relatedRefreshGeneration, setRelatedRefreshGeneration] = useState(0)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [returnReviewOpen, setReturnReviewOpen] = useState(false)
  const [hideOpen, setHideOpen] = useState(false)
  const [partsOpen, setPartsOpen] = useState(false)
  const [partsReceipt, setPartsReceipt] = useState('')
  const [partsAction, setPartsAction] = useState<OfflineAction | null>(null)
  const [partsHydrated, setPartsHydrated] = useState(!sync)
  const [partsHydrationError, setPartsHydrationError] = useState(false)
  const [partsHydrationAttempt, setPartsHydrationAttempt] = useState(0)
  const partsHydrationKey = useRef('')
  const syncRef = useRef(sync)
  const hasSync = Boolean(sync)
  const actionTrackingReady = sync?.actionTrackingReady
  const [legacyDisclosureOpenId, setLegacyDisclosureOpenId] = useState<string | undefined>()
  const [partsFocusRequest, setPartsFocusRequest] = useState(0)
  const partsRef = useRef<HTMLDivElement>(null)
  const [taskActionHost, setTaskActionHost] = useState<HTMLElement | null>(null)
  const [hideReason, setHideReason] = useState('')
  const [taskControlBusy, setTaskControlBusy] = useState(false)
  const [taskControlMessage, setTaskControlMessage] = useState('')
  const [taskControlError, setTaskControlError] = useState('')
  useEffect(() => { syncRef.current = sync }, [sync])
  useEffect(() => {
    setPartsOpen(false); setPartsReceipt(''); setPartsAction(null); setPartsFocusRequest(0)
    setLegacyDisclosureOpenId(undefined); setPartsHydrated(!hasSync); setPartsHydrationError(false); setPartsHydrationAttempt(0); partsHydrationKey.current = ''
  }, [accessPrefix, hasSync, issueKey])
  useEffect(() => {
    const currentSync = syncRef.current
    if (!currentSync || !issueKey) { setPartsHydrated(true); return }
    if (actionTrackingReady === false) { setPartsHydrated(false); return }
    const hydrationKey = `${accessPrefix}:${issueKey}`
    if (partsHydrationKey.current === hydrationKey) { setPartsHydrated(true); return }
    let active = true
    setPartsHydrated(false)
    setPartsHydrationError(false)
    void (currentSync.findAction?.(issueKey, 'inventory_writeoff') ?? Promise.resolve(undefined)).then(action => {
      if (active) {
        if (action) setPartsAction(action)
        partsHydrationKey.current = hydrationKey
        setPartsHydrated(true)
      }
    }).catch(() => {
      if (active) {
        setPartsHydrationError(true)
        setPartsHydrated(false)
      }
    })
    return () => { active = false }
  }, [accessPrefix, actionTrackingReady, hasSync, issueKey, partsHydrationAttempt])
  const retryPartsHydration = () => {
    partsHydrationKey.current = ''
    setPartsHydrationError(false)
    setPartsHydrationAttempt(value => value + 1)
  }
  const partsActionId = partsAction?.id
  useEffect(() => {
    if (!sync || !partsActionId) return
    return sync.subscribeAction?.(partsActionId, action => {
      if (!action || action.resourceId !== issueKey || action.action !== 'inventory_writeoff') return
      setPartsAction(action)
      if (action.state === 'confirmed') {
        setPartsReceipt('Запчасть списана')
        setPartsOpen(false)
        setLegacyDisclosureOpenId(undefined)
        setPartsAction(null)
      }
    })
  }, [issueKey, partsActionId, sync])
  useLayoutEffect(() => {
    if (!partsOpen || partsFocusRequest === 0 || !partsRef.current) return
    partsRef.current.focus({ preventScroll: true })
    partsRef.current.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [partsFocusRequest, partsOpen])
  const location = useLocation()
  const hasOwnedView = user.role === 'mechanic' || user.role === 'operator'
  const [taskView, setTaskView] = useState<'queue' | 'mine'>(() =>
    hasOwnedView && new URLSearchParams(location.search).get('view') === 'mine' ? 'mine' : 'queue')
  useEffect(() => {
    setTaskView(hasOwnedView && new URLSearchParams(location.search).get('view') === 'mine' ? 'mine' : 'queue')
  }, [hasOwnedView, location.search])
  const ownedEnabled = user.role === 'mechanic' || (user.role === 'operator' && taskView === 'mine')
  const mutationKeys = useRef(new StableMutationKey())

  useLayoutEffect(() => () => { ++ownerGeneration.current }, [])

  const observeAuthorizationFailure = useCallback((error: unknown) => {
    const failure = classifyApiError(error, 'Не удалось загрузить рабочие данные.')
    if (!isAuthorizationFailure(failure) || blockedRef.current) return

    blockedRef.current = true
    blockedErrorRef.current = error
    resourceStore.invalidate(cachePrefix, { prefix: true })
    setAuthorizationFailure(failure)
    if (!refreshStartedRef.current) {
      refreshStartedRef.current = true
      void onAuthorizationFailure(error).catch(() => undefined)
    }
  }, [cachePrefix, onAuthorizationFailure])

  const guarded = useCallback(async <T,>(loader: () => Promise<T>): Promise<T> => {
    const generation = ownerGeneration.current
    const accessGeneration = getAccessGeneration()
    if (accessGeneration < 0) throw new Error('work_access_changed')
    if (blockedRef.current) {
      throw blockedErrorRef.current ?? new Error('work_authorization_blocked')
    }
    try {
      const value = await loader()
      if (getAccessGeneration() !== accessGeneration) throw new Error('work_access_changed')
      if (blockedRef.current) {
        throw blockedErrorRef.current ?? new Error('work_authorization_blocked')
      }
      return value
    } catch (error) {
      if (generation === ownerGeneration.current && getAccessGeneration() === accessGeneration) observeAuthorizationFailure(error)
      throw error
    }
  }, [getAccessGeneration, observeAuthorizationFailure])

  const list = useCachedResource(
    listKey,
    () => guarded(() => loadWorkPage(apiClient, requestState, selectedPark.tag)),
  )
  const owned = useCachedResource<Paged<TrackerIssue>>(
    ownedKey,
    () => guarded(() => apiClient.trackerIssues({
      owned_by_me: true,
      open_only: true,
      sort: 'oldest',
      limit: 50,
      offset: 0,
    }).then(page => ({ ...page, items: oldestFirst(page.items) }))),
    { enabled: ownedEnabled && Boolean(user.username.trim()) },
  )
  const detail = useCachedResource<TrackerIssueDetail>(
    detailKey,
    () => guarded(() => includeHidden
      ? apiClient.trackerIssue(issueKey as string, undefined, true)
      : apiClient.trackerIssue(issueKey as string)),
    { enabled: Boolean(issueKey) },
  )
  const hiddenDetail = Boolean(detail.data?.workflow?.hidden)
  const comments = useCachedResource<TaskTimelineItem[]>(
    commentsKey,
    () => guarded(async () => apiClient.taskTimeline
      ? apiClient.taskTimeline(issueKey as string)
      : (await apiClient.trackerComments(issueKey as string)).map(comment => ({
          id: comment.id, kind: 'tracker' as const,
          author: comment.author ?? comment.author_login ?? 'Tracker', text: comment.text,
          created_at: comment.created_at ?? '', sync_state: 'synced' as const,
          attachments: comment.attachments ?? [],
        }))),
    { enabled: Boolean(issueKey && (!includeHidden || (detail.data && !hiddenDetail))) },
  )
  const defectCodes = useCachedResource(
    `${accessPrefix}defect-codes`,
    () => guarded(() => apiClient.taskDefectCodes ? apiClient.taskDefectCodes() : Promise.resolve([])),
    { enabled: Boolean(issueKey && user.role === 'mechanic') },
  )
  const robotNumber = normalizedRobotNumber(detail.data?.robot)
  const relatedQueue = detail.data?.queue?.trim()
    || selectedPark.tracker_queue?.trim()
    || requestState.filters.queue?.trim()
  const relatedPark = requestState.filters.untagged ? undefined : selectedPark.tag
  const relatedPrefix = robotNumber && relatedQueue
    ? `${accessPrefix}related:${issueKey}:${relatedQueue}:${relatedPark ?? 'untagged'}:${robotNumber}`
    : ''
  const transitionsEnabled = false
  const transitionsKey = transitionsEnabled
    ? `${accessPrefix}transitions:${issueKey}`
    : ''
  const transitions = useCachedResource(
    transitionsKey,
    () => guarded(() => apiClient.trackerTransitions(issueKey as string)),
    { enabled: transitionsEnabled },
  )

  // Coalesced same-access reads can outlive their initiating render (including
  // StrictMode cleanup). Only the current resource owner handles their denial.
  useEffect(() => {
    for (const error of [list.error, owned.error, detail.error, comments.error, transitions.error]) {
      if (error) observeAuthorizationFailure(error)
    }
  }, [comments.error, detail.error, list.error, observeAuthorizationFailure, owned.error, transitions.error])

  const listFailure = failureFor(
    list.error,
    'Не удалось загрузить очередь задач.',
  )
  const ownedItems = ownedEnabled ? oldestFirst(owned.data?.items ?? []) : []
  const ownedKeys = new Set(ownedItems.map(item => item.key))
  const detailFailure = failureFor(
    detail.error,
    'Не удалось загрузить задачу.',
  )
  const commentsFailure = failureFor(
    comments.error,
    'Не удалось загрузить комментарии.',
  )
  const transitionsFailure = failureFor(
    transitions.error,
    'Не удалось загрузить действия задачи.',
  )

  useEffect(() => {
    if (!issueKey) return
    if (detailFailure?.kind !== 'not-found') return
    resourceStore.invalidate(detailKey)
    resourceStore.invalidate(commentsKey)
    resourceStore.invalidate(`${accessPrefix}transitions:${issueKey}`)
    resourceStore.invalidate(`${accessPrefix}related:${issueKey}:`, { prefix: true })
  }, [accessPrefix, commentsKey, detailFailure?.kind, detailKey, issueKey])

  const search = buildWorkSearch({ ...state, rootIssue: undefined, detailTab: undefined, checkTab: undefined }, selectedPark.id)
  const requestedTab = state.detailTab === 'parts' ? 'task' : state.detailTab ?? 'task'
  const mechanicCanWork = Boolean(
    detail.data && (user.role !== 'mechanic' || mechanicOwnsIssue(user, detail.data)),
  )
  const claimParkId = detail.data?.claim?.park_id ?? null
  const taskParkId = user.parks.some(park => park.id === claimParkId) ? claimParkId : null
  const activeTab = requestedTab === 'check' && !mechanicCanWork ? 'task' : requestedTab
  const changeTab = (detailTab: 'task' | 'open' | 'closed' | 'check') => onStateChange({ ...state, detailTab }, { replace: false })
  const rootIssue = state.rootIssue ?? issueKey
  const rootHref = rootIssue ? workIssueHref(rootIssue, { ...state, rootIssue: undefined, detailTab: undefined, checkTab: undefined }, selectedPark.id) : ''
  const listDataAvailable = list.data !== undefined
  const listScrollRef = useRef<HTMLDivElement>(null)
  const savedOnOpenRef = useRef(false)
  useLayoutEffect(() => {
    const element = listScrollRef.current
    if (!element) return undefined
    const documentScrolled = window.innerWidth < 900
    // Sequential detail has no visible list; it must not restore or overwrite
    // the list's saved document position during its own mount/cleanup.
    if (documentScrolled && issueKey) return undefined
    const position = readWorkScroll(user.id, search)
    let restored = !documentScrolled
    let frame: number | undefined
    const timer = documentScrolled ? window.setTimeout(() => {
      // Shell route focus runs after commit. Restore once after that focus,
      // leaving browser history policy and the desktop inner scroller alone.
      frame = window.requestAnimationFrame(() => {
        window.scrollTo({ top: position, behavior: 'instant' })
        restored = true
      })
    }, 0) : undefined
    if (!documentScrolled) element.scrollTop = position
    return () => {
      if (timer != null) window.clearTimeout(timer)
      if (frame != null) window.cancelAnimationFrame(frame)
      // StrictMode can clean up a freshly mounted cached list before its
      // deferred restoration. Never replace its saved position in that gap.
      if (restored && !savedOnOpenRef.current) {
        saveWorkScroll(user.id, search, documentScrolled ? window.scrollY : element.scrollTop)
      }
    }
  }, [issueKey, listDataAvailable, search, user.id])

  const saveAndOpenIssue = (key: string) => {
    const position = window.innerWidth < 900 ? window.scrollY : listScrollRef.current?.scrollTop ?? 0
    saveWorkScroll(user.id, search, position)
    savedOnOpenRef.current = window.innerWidth < 900
    onOpenIssue(key)
  }

  const invalidateMutationResources = useCallback((refreshComments = true) => {
    resourceStore.cancelPending(`${accessPrefix}list:${selectedPark.id}:`, { prefix: true })
    resourceStore.cancelPending(ownedKey)
    resourceStore.revalidate(`${accessPrefix}list:${selectedPark.id}:`, { prefix: true })
    resourceStore.revalidate(ownedKey)
    if (!issueKey) return
    resourceStore.cancelPending(detailKey)
    if (refreshComments) resourceStore.cancelPending(commentsKey)
    resourceStore.cancelPending(`${accessPrefix}transitions:${issueKey}`)
    resourceStore.cancelPending(`${accessPrefix}related:${issueKey}:`, { prefix: true })
    resourceStore.revalidate(detailKey)
    if (refreshComments) resourceStore.revalidate(commentsKey)
    resourceStore.revalidate(`${accessPrefix}transitions:${issueKey}`)
    resourceStore.revalidate(`${accessPrefix}related:${issueKey}:`, { prefix: true })
    setRelatedRefreshGeneration((generation) => generation + 1)
    void Promise.allSettled([
      list.refresh(),
      ...(ownedEnabled ? [owned.refresh()] : []),
      detail.refresh(),
      ...(refreshComments ? [comments.refresh()] : []),
      ...(transitionsEnabled ? [transitions.refresh()] : []),
    ])
  }, [
    accessPrefix,
    comments,
    commentsKey,
    detail,
    detailKey,
    issueKey,
    list,
    owned,
    ownedKey,
    selectedPark.id,
    transitions,
    transitionsEnabled,
    ownedEnabled,
  ])

  const mutate = useCallback(async (action: (assertCurrent: () => void) => Promise<unknown>, onSuccess?: () => void, refreshComments = true) => {
    const generation = ownerGeneration.current
    const accessGeneration = getAccessGeneration()
    const assertCurrent = () => {
      if (generation !== ownerGeneration.current || getAccessGeneration() !== accessGeneration || accessGeneration < 0 || blockedRef.current) throw new Error('work_access_changed')
    }
    try {
      await guarded(() => action(assertCurrent))
    } catch (error) {
      if (generation === ownerGeneration.current && getAccessGeneration() === accessGeneration
        && error instanceof ApiError && error.detail === 'tracker_state_conflict') invalidateMutationResources()
      throw error
    }
    if (generation !== ownerGeneration.current || getAccessGeneration() !== accessGeneration) return
    invalidateMutationResources(refreshComments)
    onSuccess?.()
  }, [getAccessGeneration, guarded, invalidateMutationResources])

  const lifecycleMutation = useCallback(async <T,>(action: string, payload: unknown, request: (key: string) => Promise<T>): Promise<T> => {
    const serialized = JSON.stringify(payload)
    const key = mutationKeys.current.get(action, serialized)
    const result = await request(key)
    mutationKeys.current.succeeded(action, serialized)
    return result
  }, [])

  const enqueueComment = useCallback(async (text: string) => {
    if (!sync || !issueKey || taskParkId == null) throw new Error('offline_sync_unavailable')
    const serialized = JSON.stringify({ text })
    const id = mutationKeys.current.get('message', serialized)
    const pending = buildCommentAction({ issueKey, parkId: taskParkId, id, author: user.username, text })
    await sync.enqueueAction(pending.action)
    mutationKeys.current.succeeded('message', serialized)
    const current = resourceStore.get<TaskTimelineItem[]>(commentsKey) ?? comments.data ?? []
    if (!current.some(item => item.id === pending.timelineItem.id)) {
      resourceStore.set(commentsKey, [...current, pending.timelineItem], false)
    }
    return id
  }, [comments.data, commentsKey, issueKey, sync, taskParkId, user.username])

  const enqueueHandoff = useCallback(async (value: { assignee: string; reason: string; done?: string; remaining?: string; obstacles?: string }) => {
    if (!sync || !issueKey || taskParkId == null) throw new Error('offline_sync_unavailable')
    const serialized = JSON.stringify(value)
    const id = mutationKeys.current.get('handoff', serialized)
    const pending = buildHandoffAction({ issueKey, parkId: taskParkId, id, ...value })
    await sync.enqueueAction(pending.action)
    mutationKeys.current.succeeded('handoff', serialized)
    const current = resourceStore.get<TaskTimelineItem[]>(commentsKey) ?? comments.data ?? []
    if (!current.some(item => item.id === pending.timelineItem.id)) {
      resourceStore.set(commentsKey, [...current, pending.timelineItem], false)
    }
  }, [comments.data, commentsKey, issueKey, sync, taskParkId])

  const runTaskControl = useCallback(async (
    action: string,
    payload: unknown,
    request: (key: string) => Promise<unknown>,
    message: string,
    onSuccess?: () => void,
    refreshComments = true,
  ) => {
    if (taskControlBusy) return
    setTaskControlBusy(true)
    setTaskControlMessage('')
    setTaskControlError('')
    try {
      await mutate(() => lifecycleMutation(action, payload, request), onSuccess, refreshComments)
      setTaskControlMessage(message)
    } catch (error) {
      setTaskControlError(classifyApiError(error, 'Не удалось изменить задачу.').description)
    } finally {
      setTaskControlBusy(false)
    }
  }, [lifecycleMutation, mutate, taskControlBusy])

  const effectiveCommentsFailure = hiddenDetail ? null : commentsFailure
  const detailSideFailure = useMemo(() => {
    const failures = [
      detailFailure,
      effectiveCommentsFailure,
      transitionsFailure,
    ].filter((failure): failure is DomainError => failure != null)
    return failures.find((failure) => !canRetainProtectedData(failure))
      ?? failures[0]
      ?? null
  }, [detailFailure, effectiveCommentsFailure, transitionsFailure])
  const detailSideDataAvailable = Boolean(
    detail.data
      && (hiddenDetail || comments.data !== undefined)
      && (!transitionsEnabled || transitions.data !== undefined),
  )
  const canRenderDetailActions = Boolean(
      detail.data
      && mechanicCanWork
      && !hiddenDetail
      && (
        !detailSideFailure
        || (
          detailSideDataAvailable
          && canRetainProtectedData(detailSideFailure)
        )
      ),
  )
  const taskComments = comments.data ?? []
  const hasQualifyingComment = detail.data?.workflow?.has_current_cycle_comment ?? false
  if (authorizationFailure) {
    return (
      <ErrorState
        description={authorizationFailure.description}
        requestId={authorizationFailure.requestId}
        title={authorizationFailure.title}
      />
    )
  }

  return (
    <div className="rp-work-domain" data-has-detail={Boolean(issueKey)}>
      <WorkFilters
        driver={user.role === 'driver'}
        key={buildWorkSearch(state, null)}
        loading={list.isRevalidating}
        manager={manager}
        onApply={(next) => onStateChange({ ...state, ...next }, { replace: false })}
        value={requestState}
      />

      <div
        className="rp-workbench"
        data-has-detail={Boolean(issueKey)}
      >
        <MasterDetail
          detail={<div className="rp-work-detail-pane" data-task-view={taskFirst ? taskFocus : 'all'}>
            <Panel collapsible density="work" storageKey="work-detail" title={issueKey ? `Задача ${issueKey}` : 'Детали задачи'}>
            {!issueKey ? (
              <EmptyState
                description="Выберите задачу в очереди, чтобы увидеть подробности."
                icon="work"
                title="Задача не выбрана"
              />
            ) : (
              <ResourceBoundary
                dataAvailable={detailSideDataAvailable}
                failure={detailSideFailure}
                onRetry={() => void Promise.allSettled([
                  detail.refresh(),
                  ...(!hiddenDetail ? [comments.refresh()] : []),
                  ...(transitionsEnabled ? [transitions.refresh()] : []),
                ])}
              >
                {detail.isLoading && !detail.data ? (
                  <LoadingState label="Загружаем задачу" />
                ) : (
                  <>
                    {detail.data ? <>
                      {state.rootIssue && state.rootIssue !== issueKey ? (
                      <nav className="rp-work-origin" aria-label="Возврат к главному блокеру">
                        <Link to={rootHref}>К главному блокеру {rootIssue}</Link>
                      </nav>
                      ) : null}
                      <TaskFirstWorkbench enabled={taskFirst} activeTab={activeTab as WorkSection}
                        focus={taskFocus} canCheck={mechanicCanWork}
                        onChange={tab => {
                          setTaskFocus(tab === 'chat' ? 'chat' : 'repair')
                          changeTab(tab === 'chat' ? 'task' : tab)
                        }} />
                    </> : null}
                    <TabPanel id="work-panel-task" labelledBy={taskFirst && taskFocus === 'chat' ? 'tab-chat' : 'tab-task'} active={activeTab === 'task'} key={issueKey}>
                    <TaskFirstTaskLayout enabled={taskFirst} onActionHost={setTaskActionHost} header={detail.data && (taskFirst || detail.data.workflow) ? <>
                      {taskFirst ? <h1 className="a-task-title">Задача {detail.data.key}</h1> : null}
                      <SyncStatus updatedAt={hiddenDetail ? detail.updatedAt : detail.updatedAt !== null && comments.updatedAt !== null ? Math.min(detail.updatedAt, comments.updatedAt) : null}
                        isRevalidating={detail.isRevalidating || (!hiddenDetail && comments.isRevalidating)}
                        error={detail.error || (!hiddenDetail ? comments.error : null)} />
                      <TaskIssueSummary issue={detail.data} now={now} robotReadOnly={!mechanicCanWork}
                        onOpenRobotCheck={mechanicCanWork ? () => changeTab('check') : undefined} />
                      {detail.data.workflow ? <TaskSyncStatus state={detail.data.workflow.sync_state} errorCode={detail.data.workflow.sync_error_code} /> : null}
                    </> : null}>
                    {detail.data?.workflow ? <>
                      {taskFirst && taskFocus === 'repair' ? <TaskRepairSequence canCheck={mechanicCanWork} onCheck={() => changeTab('check')} onParts={() => {
                        setPartsOpen(true)
                        setPartsFocusRequest(value => value + 1)
                      }} /> : null}
                      {manager ? <section aria-label="Управление задачей" className="issue-section">
                        {detail.data.workflow.hidden ? <>
                          <p>Причина скрытия: {detail.data.workflow.hidden.reason}</p>
                          <Button busy={taskControlBusy} onClick={() => void runTaskControl(
                            'restore-task', {}, key => apiClient.taskRestore!(detail.data!.key, key),
                            'Задача восстановлена',
                          )} variant="secondary">Восстановить задачу</Button>
                        </> : <>
                          {detail.data.workflow.sync_state === 'needs_attention' ? <Button busy={taskControlBusy} onClick={() => void runTaskControl(
                            'retry-now', {}, key => apiClient.taskRetryNow!(detail.data!.key, key),
                            'Повторная отправка запущена',
                          )} variant="secondary">Повторить сейчас</Button> : null}
                          {!hideOpen ? <Button onClick={() => setHideOpen(true)} variant="secondary">Скрыть задачу</Button> : <div className="form-grid">
                            <label className="field"><span className="field-label">Причина скрытия</span><textarea maxLength={4000} onChange={event => setHideReason(event.target.value)} value={hideReason} /></label>
                            <div className="form-actions"><Button busy={taskControlBusy} disabled={!hideReason.trim()} onClick={() => void runTaskControl(
                              'hide-task', { reason: hideReason.trim() }, key => apiClient.taskHide!(detail.data!.key, hideReason.trim(), key),
                              'Задача скрыта', includeHidden ? undefined : onCloseIssue, false,
                            )} variant="danger">Подтвердить скрытие</Button><Button onClick={() => { setHideOpen(false); setHideReason('') }} variant="secondary">Отмена</Button></div>
                          </div>}
                        </>}
                        {taskControlMessage ? <p role="status">{taskControlMessage}</p> : null}
                        {taskControlError ? <p role="alert">{taskControlError}</p> : null}
                      </section> : null}
                      {!hiddenDetail ? <div className="a-work-chat"><TaskTimeline items={taskComments} /></div> : null}
                    </> : taskFirst ? <div className="a-work-chat"><TaskTimeline items={taskComments} /></div> : <IssueDetailPanel
                      currentUser={user.tracker_login ?? user.username} accountKey={user.username}
                      commentsLoading={comments.isLoading && !comments.data}
                      comments={taskComments.map(item => ({
                        id: item.id, text: item.text, author: item.author,
                        created_at: item.created_at, attachments: item.attachments,
                      }))} issue={detail.data ?? null} loading={detail.isLoading && !detail.data}
                      showRobotCheck={false} robotReadOnly={!mechanicCanWork}
                      onOpenRobotCheck={mechanicCanWork ? () => changeTab('check') : undefined} />}
                    {detail.data && user.role === 'mechanic' && !mechanicCanWork ? (
                      <p className="panel-hint" role="status">
                        {detail.data.assignee
                          ? `Задача сейчас у ${detail.data.assignee.display}. Для изменений возьмите задачу вместо сменщика в списке.`
                          : 'Для изменений сначала возьмите задачу в работу в списке.'}
                      </p>
                    ) : null}
                    {detail.data && !detail.data.workflow ? (
                      <div className="panel-hint" role="status"><span>Актуальное состояние задачи пока недоступно; изменения временно заблокированы.</span>{' '}<Button onClick={() => void detail.refresh()} size="compact" variant="secondary">Повторить загрузку</Button></div>
                    ) : null}
                    {canRenderDetailActions && detail.data?.workflow ? (
                      <IssueActionsPanel
                        actionHost={taskFirst ? taskActionHost : null}
                        capabilities={detail.data.capabilities}
                        draftOwner={user.username}
                        currentUser={user.tracker_login ?? user.username}
                        issueKey={detail.data.key}
                        role={detail.data.workflow ? user.role : undefined}
                        reviewState={detail.data.workflow?.review_state}
                        onAssign={(assignee) => mutate(
                          (assertCurrent) => runTrackerSubmission(user.username, detail.data!, 'assign', { assignee }, headers => apiClient.trackerAssign(detail.data!.key, assignee, headers), assertCurrent),
                        )}
                        onAttach={detail.data.workflow
                          ? apiClient.taskPhoto ? (file) => mutate(async assertCurrent => {
                            const identity = JSON.stringify([detail.data!.key, user.id, await attachmentIdentity(file)])
                            assertCurrent()
                            await lifecycleMutation('photo', identity, key => apiClient.taskPhoto!(detail.data!.key, file, key))
                          }) : undefined
                          : (file) => mutate(
                            async (assertCurrent) => runTrackerSubmission(user.username, detail.data!, 'attach', await attachmentIdentity(file), headers => apiClient.trackerAttach(detail.data!.key, file, headers), assertCurrent),
                          )}
                        onClose={detail.data.workflow ? async () => undefined : () => mutate((assertCurrent) => runTrackerSubmission(user.username, detail.data!, 'close', {}, headers => apiClient.trackerClose(detail.data!.key, headers), assertCurrent), onCloseIssue)}
                        onComment={(text) => sync && detail.data!.workflow && taskParkId != null
                          ? enqueueComment(text).then(() => undefined)
                          : mutate(
                            (assertCurrent) => detail.data!.workflow && apiClient.taskMessage
                              ? lifecycleMutation('message', text, key => apiClient.taskMessage!(detail.data!.key, text, key))
                              : runTrackerSubmission(user.username, detail.data!, 'comment', { text }, headers => apiClient.trackerComment(detail.data!.key, text, headers), assertCurrent),
                          )}
                        onSubmitReview={async () => { setReviewOpen(true) }}
                        onReturnReview={async () => { setReturnReviewOpen(true) }}
                        onApproveReview={async () => {
                          if (apiClient.taskApproveReview) await mutate(() => lifecycleMutation('approve-review', {}, key => apiClient.taskApproveReview!(detail.data!.key, key)), onCloseIssue)
                        }}
                        onTransition={detail.data.workflow ? async () => undefined : (transition) => mutate(
                          (assertCurrent) => runTrackerSubmission(user.username, detail.data!, 'transition', { transition }, headers => apiClient.trackerTransition(detail.data!.key, transition, undefined, headers), assertCurrent),
                        )}
                        onUnassign={() => mutate(
                          (assertCurrent) => runTrackerSubmission(user.username, detail.data!, 'unassign', {}, headers => apiClient.trackerUnassign(detail.data!.key, headers), assertCurrent),
                        )}
                        transitions={transitions.data ?? []}
                      />
                    ) : null}
                    {returnReviewOpen && detail.data?.workflow?.review_state === 'pending' && user.role !== 'mechanic' ? <ReturnReviewForm key={detail.data.key}
                      onCancel={() => { setReturnReviewOpen(false); mutationKeys.current.cancel('return-review') }}
                      onSubmit={async reason => {
                        if (!apiClient.taskReturnReview) throw new Error('Return action unavailable')
                        await mutate(() => lifecycleMutation('return-review', { reason }, key => apiClient.taskReturnReview!(detail.data!.key, reason, undefined, key)))
                        setReturnReviewOpen(false)
                      }} /> : null}
                    {reviewOpen && detail.data && user.role === 'mechanic' ? <SubmitReviewForm key={detail.data.key}
                      onCancel={() => { setReviewOpen(false); mutationKeys.current.cancel('submit-review') }}
                      defectCodes={defectCodes.data ?? []} hasQualifyingComment={Boolean(hasQualifyingComment)}
                      onSubmit={async value => {
                        if (!apiClient.taskSubmitReview) return
                        const payload = { defectCode: value.defectCode, comment: value.comment ?? '', photo: {
                          name: value.photo.name, type: value.photo.type, size: value.photo.size, lastModified: value.photo.lastModified,
                        } }
                        if (sync?.enqueueMedia && taskParkId != null) {
                          const serialized = JSON.stringify(payload)
                          const reviewId = mutationKeys.current.get('submit-review', serialized)
                          const mediaId = `media-${reviewId}`
                          const prepared = await prepareImage(value.photo)
                          try {
                            await sync.enqueueMedia({
                              id: mediaId,
                              actionId: reviewId,
                              issueKey: detail.data!.key,
                              name: value.photo.name,
                              blob: prepared.blob,
                              mimeType: prepared.mimeType,
                              sha256: prepared.sha256,
                              sizeBytes: prepared.sizeBytes,
                            })
                            const commentId = value.comment?.trim() ? await enqueueComment(value.comment) : null
                            await sync.enqueueAction(buildSubmitReviewAction({
                              issueKey: detail.data!.key,
                              parkId: taskParkId,
                              id: reviewId,
                              defectCode: value.defectCode,
                              mediaActionId: mediaId,
                              commentActionId: commentId,
                            }))
                            mutationKeys.current.succeeded('submit-review', serialized)
                          } finally {
                            prepared.releasePreview()
                          }
                        } else {
                          await mutate(() => lifecycleMutation('submit-review', payload, key => apiClient.taskSubmitReview!(detail.data!.key, value, key)))
                        }
                        setReviewOpen(false)
                      }} /> : null}
                    {detail.data?.workflow ? <div aria-label="Дополнительные разделы задачи" className="rp-responsive-disclosure-group" role="group">
                      {user.role === 'mechanic' && mechanicCanWork ? <>
                        <ClosedDisclosure title="Списать запчасть" open={partsOpen} onOpenChange={open => {
                          if (open) setPartsReceipt('')
                          setPartsOpen(open)
                        }}>
                          <div id="parts" ref={partsRef} tabIndex={-1}>
                            <TaskPartsPanel actionHydrationError={partsHydrationError} apiClient={apiClient} enqueueAction={sync?.enqueueAction} hydratingAction={!partsHydrated} issueKey={detail.data.key} onQueued={setPartsAction} onRetryActionHydration={retryPartsHydration} queuedAction={partsAction} onWritten={receipt => {
                              setPartsReceipt(receipt)
                              setPartsOpen(false)
                              if (!sync) void comments.refresh()
                            }} parkId={taskParkId} />
                          </div>
                        </ClosedDisclosure>
                        {partsReceipt ? <p role="status">{partsReceipt}</p> : null}
                      </> : null}
                      <ClosedDisclosure title="Передать смену">
                        <div id="handoff">
                          <EmbeddedTaskCollaboration issueKey={detail.data.key} owner={user.username}
                            active={activeTab === 'task' && mechanicCanWork}
                            canWrite={detail.data.capabilities.comment && mechanicCanWork}
                            lifecycle
                            onHandoff={value => sync && taskParkId != null
                              ? enqueueHandoff(value)
                              : mutate(() => lifecycleMutation('handoff', value, key => apiClient.taskHandoff!(detail.data!.key, value, key)))}
                            onAuthorizationFailure={observeAuthorizationFailure} />
                        </div>
                      </ClosedDisclosure>
                    </div> : detail.data ? <><ResponsiveDisclosureGroup controlledOpenId={legacyDisclosureOpenId ?? null} label="Дополнительные разделы задачи" onOpenIdChange={setLegacyDisclosureOpenId}>
                      {user.role === 'mechanic' && mechanicCanWork ? <ResponsiveDisclosure id="parts" onOpenChange={open => { if (open) setPartsReceipt('') }} title="Использовать запчасть"><TaskPartsPanel actionHydrationError={partsHydrationError} apiClient={apiClient} enqueueAction={sync?.enqueueAction} hydratingAction={!partsHydrated} issueKey={detail.data.key} onQueued={setPartsAction} onRetryActionHydration={retryPartsHydration} queuedAction={partsAction} onWritten={receipt => { setPartsReceipt(receipt); setLegacyDisclosureOpenId(undefined); if (!sync) void comments.refresh() }} parkId={taskParkId} /></ResponsiveDisclosure> : null}
                      <ResponsiveDisclosure id="handoff" title="Передача смены"><EmbeddedTaskCollaboration issueKey={detail.data.key} owner={user.username} active={activeTab === 'task' && mechanicCanWork} canWrite={detail.data.capabilities.comment && mechanicCanWork} onAuthorizationFailure={observeAuthorizationFailure} /></ResponsiveDisclosure>
                    </ResponsiveDisclosureGroup>{partsReceipt ? <p role="status">{partsReceipt}</p> : null}</> : null}
                    </TaskFirstTaskLayout>
                    </TabPanel>
                    {(['open', 'closed'] as const).map(kind => <TabPanel key={kind} id={`work-panel-${kind}`} labelledBy={`tab-${kind}`} active={activeTab === kind}>
                      {activeTab === kind && detail.data ? robotNumber && relatedPrefix && relatedQueue ? <RelatedTasksPanel
                        apiClient={apiClient} issueKey={issueKey ?? ''} kind={kind}
                        key={`${relatedPrefix}:${relatedRefreshGeneration}:${kind}`}
                        onOpen={onOpenRelatedIssue ?? saveAndOpenIssue} park={relatedPark}
                        now={now}
                        resourcePrefix={relatedPrefix} robotNumber={robotNumber} queue={relatedQueue}
                      /> : <p>Робот в задаче не указан — связанные задачи недоступны.</p> : null}
                    </TabPanel>)}
                    {mechanicCanWork ? <TabPanel id="work-panel-check" labelledBy="tab-check" active={activeTab === 'check'}>
                      {detail.data ? robotNumber ? <WorkRobotCheck
                        key={relatedPrefix} robot={robotNumber} user={user} activeTab={state.checkTab}
                        onAuthorizationFailure={failure => {
                          if (failure.kind === 'unauthorized') observeAuthorizationFailure(new ApiError(401, null, failure.requestId))
                        }}
                        onOpenTasks={() => changeTab('open')}
                        onTabChange={checkTab => onStateChange({ ...state, checkTab }, { replace: true })}
                      /> : <p>Робот в задаче не указан — проверка недоступна.</p> : null}
                    </TabPanel> : null}
                  </>
                )}
              </ResourceBoundary>
            )}
            </Panel>
          </div>}
          detailOpen={Boolean(issueKey)}
          list={<div className="rp-work-list-pane">
            <h2>{taskView === 'mine' && hasOwnedView ? 'Мои задачи' : 'Очередь задач'}</h2>
            {hasOwnedView ? <div aria-label="Раздел задач" className="rp-work-view-switch">
              <button aria-pressed={taskView === 'queue'} onClick={() => setTaskView('queue')} type="button">Очередь</button>
              <button aria-pressed={taskView === 'mine'} onClick={() => setTaskView('mine')} type="button">Мои задачи ({ownedItems.length})</button>
            </div> : null}
            <SyncStatus {...list} />
            <ResourceBoundary
              dataAvailable={list.data !== undefined}
              failure={listFailure}
              onRetry={() => void list.refresh()}
            >
              {list.isLoading && !list.data ? (
                <LoadingState label="Загружаем очередь задач" />
              ) : taskView === 'mine' && hasOwnedView ? (
                owned.isLoading && !owned.data ? <LoadingState label="Загружаем мои задачи" /> :
                ownedItems.length ? <div className="rp-work-list-scroll" ref={listScrollRef}>
                  <h3>{user.role === 'operator' ? 'Ждут проверки' : 'Мои задачи в работе'}</h3>
                  <WorkIssueRows apiClient={apiClient} items={ownedItems}
                    onClaimed={() => { void list.refresh(); void owned.refresh() }}
                    onOpen={saveAndOpenIssue} selected={issueKey} now={now} user={user} />
                </div> : <EmptyState description="Взятые вами задачи появятся здесь." icon="work" title="Моих задач пока нет" />
              ) : !listFailure && list.data?.items.length === 0 && ownedItems.length === 0 ? (
                <EmptyState
                  description="Измените фильтры или проверьте выбранный парк."
                  icon="work"
                  title="Нет задач"
                />
              ) : list.data ? (
                <div className="rp-work-list-scroll" ref={listScrollRef}>
                  <p className="rp-work-list-count">Показано {list.data.items.length}{list.data.total > list.data.items.length ? ` из ${list.data.total}` : ''}</p>
                  {ownedItems.length ? <>
                    <h3>{user.role === 'operator' ? 'Ждут проверки' : 'Мои задачи в работе'}</h3>
                    <WorkIssueRows
                      apiClient={apiClient}
                      items={ownedItems}
                      onClaimed={() => { void list.refresh(); void owned.refresh() }}
                      onOpen={saveAndOpenIssue}
                      selected={issueKey}
                      now={now}
                      user={user}
                    />
                    <h3>Очередь парка</h3>
                  </> : null}
                  <WorkIssueRows
                    apiClient={apiClient}
                    items={oldestFirst(list.data.items).filter(item => !ownedKeys.has(item.key))}
                    onClaimed={() => { void list.refresh(); void owned.refresh() }}
                    onOpen={saveAndOpenIssue}
                    selected={issueKey}
                    now={now}
                    user={user}
                  />
                </div>
              ) : null}

              {list.data && taskView === 'queue' ? (
                <nav aria-label="Страницы задач" className="rp-work-pagination">
                  <Button
                    aria-label="Предыдущая страница"
                    disabled={state.page <= 1 || list.isRevalidating}
                    onClick={() => onStateChange(
                      { ...state, page: Math.max(1, state.page - 1) },
                      { replace: false },
                    )}
                    variant="secondary"
                  >
                    Назад
                  </Button>
                  <span>Страница {state.page}</span>
                  <Button
                    aria-label="Следующая страница"
                    disabled={!list.data.has_more || list.isRevalidating}
                    onClick={() => onStateChange(
                      { ...state, page: state.page + 1 },
                      { replace: false },
                    )}
                    variant="secondary"
                  >
                    Далее
                  </Button>
                </nav>
              ) : null}
            </ResourceBoundary>
          </div>}
          onBack={onCloseIssue}
        />
      </div>
    </div>
  )
}

export function IssueWorkbench({
  apiClient = api,
  ...props
}: IssueWorkbenchProps) {
  const accessKey = workAccessKey(props.user, props.selectedPark)
  const currentAccess = useRef({ key: accessKey, generation: 0 })
  const mounted = useRef(true)
  const getAccessGeneration = useCallback(() => mounted.current && currentAccess.current.key === accessKey
    ? currentAccess.current.generation : -1, [accessKey])
  const accessPrefix = `work:${props.user.id}:${accessKey}:`
  const committedAccessPrefix = useRef(accessPrefix)
  useLayoutEffect(() => {
    mounted.current = true
    resourceStore.activateScope('work', accessPrefix)
    return () => {
      mounted.current = false
      // Retire in-flight reads, retaining completed same-access data for navigation.
      currentAccess.current.generation += 1
      resourceStore.cancelPending(committedAccessPrefix.current, { prefix: true })
    }
  }, [])
  useLayoutEffect(() => {
    if (currentAccess.current.key !== accessKey) {
      currentAccess.current = { key: accessKey, generation: currentAccess.current.generation + 1 }
    }
    if (committedAccessPrefix.current !== accessPrefix) {
      // Retire the exited access, including pending loads, so A→B→A cannot
      // coalesce A's obsolete request. Mounted same-access navigation keeps cache.
      resourceStore.invalidate(committedAccessPrefix.current, { prefix: true })
      committedAccessPrefix.current = accessPrefix
      resourceStore.activateScope('work', accessPrefix)
    }
  }, [accessKey, accessPrefix])
  const ownerKey = [
    accessKey,
    buildWorkSearch({ ...props.state, rootIssue: undefined, detailTab: undefined, checkTab: undefined }, null),
    props.issueKey ?? '',
  ].join(':')

  if (!(props.user.permissions ?? []).includes('tracker.read')) {
    return <ErrorState title="Нет доступа" description="Нет доступа к задачам Tracker." />
  }

  return (
    <TaskController
      {...props}
      apiClient={apiClient}
      accessKey={accessKey}
      getAccessGeneration={getAccessGeneration}
      key={ownerKey}
    />
  )
}
