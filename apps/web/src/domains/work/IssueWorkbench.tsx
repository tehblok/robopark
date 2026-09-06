import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  ApiError,
  type Park,
  type Paged,
  type TrackerIssue,
  type TrackerIssueDetail,
  type User,
} from '../../api'
import { SyncStatus } from '../../design-system/status/SyncStatus'
import { IssueActionsPanel } from '../../components/tracker/IssueActionsPanel'
import { IssueDetailPanel } from '../../components/tracker/IssueDetailPanel'
import { formatAge, personName, statusTone } from '../../components/tracker/issue-utils'
import { Button } from '../../design-system/actions/Button'
import { EntityRow } from '../../design-system/data/EntityRow'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from '../../design-system/feedback/AsyncState'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { StatusBadge, type StatusTone } from '../../design-system/status/StatusBadge'
import {
  classifyApiError,
  type DomainError,
} from '../../shared/api/classifyApiError'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { Tabs, TabPanel } from '../../design-system/navigation/Tabs'
import { WorkRobotCheck } from './WorkRobotCheck'
import { WorkFilters } from './WorkFilters'
import { loadWorkPage, oldestFirst } from './workData'
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
>

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

const RELATED_PAGE_SIZE = 10

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

function issueMeta(issue: TrackerIssue): ReactNode {
  const robot = normalizedRobotNumber(issue.robot)
  const age = formatAge(issue.hours_created) || 'неизвестен'
  return <div className="rp-work-issue-meta">
    <span className="rp-work-issue-age">Возраст: <strong>{age}</strong></span>
    <span>{[
      robot ? `Робот ${robot}` : 'Робот не указан',
      'SLA: нет данных',
      `Ответственный: ${personName(issue.assignee)}`,
    ].join(' · ')}</span>
  </div>
}

function WorkIssueRows({
  items,
  selected,
  onOpen,
}: {
  items: readonly TrackerIssue[]
  selected?: string
  onOpen: (key: string) => void
}) {
  return <div className="rp-work-entities">
    {items.map((item) => <EntityRow
      actions={<Button
        aria-current={item.key === selected ? 'page' : undefined}
        aria-label={`Открыть задачу ${item.key}: ${item.summary}`}
        onClick={() => onOpen(item.key)}
        variant="secondary"
      >Открыть</Button>}
      key={item.key}
      meta={issueMeta(item)}
      status={<StatusBadge tone={issueStatusTone(item)}>{item.status}</StatusBadge>}
      statusLabel={`Статус задачи ${item.key}`}
      title={<><strong>{item.key}</strong><span> · {item.summary}</span></>}
    />)}
  </div>
}

function RelatedTaskGroup({
  title,
  empty,
  resource,
  onOpen,
}: {
  title: string
  empty: string
  resource: ReturnType<typeof useCachedResource<Paged<TrackerIssue>>>
  onOpen: (key: string) => void
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
      : data?.items.length ? <WorkIssueRows items={data.items} onOpen={onOpen} />
        : <p>{empty}</p>}
  </section>
}

function RelatedTasksPanel({ apiClient, issueKey, onOpen, park, resourcePrefix, robotNumber, queue, kind }: {
  apiClient: IssueWorkbenchApiClient; issueKey: string; onOpen: (key: string) => void
  park?: string; resourcePrefix: string; robotNumber: string; queue: string; kind: 'open' | 'closed'
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
      onOpen={onOpen} resource={related}
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

function IssueWorkbenchOwner({
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
}: Required<Pick<IssueWorkbenchProps, 'apiClient'>> & Omit<IssueWorkbenchProps, 'apiClient'> & {
  accessKey: string
  getAccessGeneration: () => number
}) {
  const cachePrefix = `work:${user.id}:`
  const accessPrefix = `${cachePrefix}${accessKey}:`
  const allowUntagged = user.role === 'operator'
    || user.role === 'admin'
    || user.role === 'royal'
  const requestState = useMemo<WorkUrlState>(() => {
    if (allowUntagged || !state.filters.untagged) return state
    const filters = { ...state.filters }
    delete filters.untagged
    return { ...state, filters }
  }, [allowUntagged, state])
  const listKey = `${accessPrefix}list:${selectedPark.id}:${JSON.stringify({ filters: requestState.filters, sort: requestState.sort, page: requestState.page })}`
  const detailKey = issueKey ? `${accessPrefix}issue:${issueKey}` : ''
  const commentsKey = issueKey ? `${accessPrefix}comments:${issueKey}` : ''
  const blockedRef = useRef(false)
  const blockedErrorRef = useRef<unknown>(null)
  const refreshStartedRef = useRef(false)
  const ownerGeneration = useRef(0)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const [relatedRefreshGeneration, setRelatedRefreshGeneration] = useState(0)

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
  const detail = useCachedResource<TrackerIssueDetail>(
    detailKey,
    () => guarded(() => apiClient.trackerIssue(issueKey as string)),
    { enabled: Boolean(issueKey) },
  )
  const comments = useCachedResource(
    commentsKey,
    () => guarded(() => apiClient.trackerComments(issueKey as string)),
    { enabled: Boolean(issueKey) },
  )
  const robotNumber = normalizedRobotNumber(detail.data?.robot)
  const relatedQueue = detail.data?.queue?.trim()
    || selectedPark.tracker_queue?.trim()
    || requestState.filters.queue?.trim()
  const relatedPark = requestState.filters.untagged ? undefined : selectedPark.tag
  const relatedPrefix = robotNumber && relatedQueue
    ? `${accessPrefix}related:${issueKey}:${relatedQueue}:${relatedPark ?? 'untagged'}:${robotNumber}`
    : ''
  const transitionsEnabled = Boolean(
    issueKey && detail.data?.capabilities.transition,
  )
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
    for (const error of [list.error, detail.error, comments.error, transitions.error]) {
      if (error) observeAuthorizationFailure(error)
    }
  }, [comments.error, detail.error, list.error, observeAuthorizationFailure, transitions.error])

  const listFailure = failureFor(
    list.error,
    'Не удалось загрузить очередь задач.',
  )
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
  const activeTab = state.detailTab ?? 'task'
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

  const invalidateMutationResources = useCallback(() => {
    resourceStore.invalidate(`${accessPrefix}list:${selectedPark.id}:`, {
      prefix: true,
    })
    if (!issueKey) return
    resourceStore.invalidate(detailKey)
    resourceStore.invalidate(commentsKey)
    resourceStore.invalidate(`${accessPrefix}transitions:${issueKey}`)
    resourceStore.invalidate(`${accessPrefix}related:${issueKey}:`, { prefix: true })
    setRelatedRefreshGeneration((generation) => generation + 1)
    void Promise.allSettled([
      list.refresh(),
      detail.refresh(),
      comments.refresh(),
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
    selectedPark.id,
    transitions,
    transitionsEnabled,
  ])

  const mutate = useCallback(async (action: () => Promise<unknown>, onSuccess?: () => void) => {
    const generation = ownerGeneration.current
    const accessGeneration = getAccessGeneration()
    await guarded(action)
    if (generation !== ownerGeneration.current || getAccessGeneration() !== accessGeneration) return
    invalidateMutationResources()
    onSuccess?.()
  }, [getAccessGeneration, guarded, invalidateMutationResources])

  const detailSideFailure = useMemo(() => {
    const failures = [
      detailFailure,
      commentsFailure,
      transitionsFailure,
    ].filter((failure): failure is DomainError => failure != null)
    return failures.find((failure) => !canRetainProtectedData(failure))
      ?? failures[0]
      ?? null
  }, [commentsFailure, detailFailure, transitionsFailure])
  const detailSideDataAvailable = Boolean(
    detail.data
      && comments.data !== undefined
      && (!transitionsEnabled || transitions.data !== undefined),
  )
  const canRenderDetailActions = Boolean(
    detail.data
      && (
        !detailSideFailure
        || (
          detailSideDataAvailable
          && canRetainProtectedData(detailSideFailure)
        )
      ),
  )

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
    <div className="rp-work-domain">
      <WorkFilters
        driver={user.role === 'driver'}
        key={buildWorkSearch(state, null)}
        loading={list.isRevalidating}
        onApply={(next) => onStateChange({ ...state, ...next }, { replace: false })}
        value={requestState}
      />

      <div
        className="rp-workbench"
        data-has-detail={Boolean(issueKey)}
      >
        <MasterDetail
          detail={<div className="rp-work-detail-pane">
            <h2>{issueKey ? `Задача ${issueKey}` : 'Детали задачи'}</h2>
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
                  comments.refresh(),
                  ...(transitionsEnabled ? [transitions.refresh()] : []),
                ])}
              >
                {detail.isLoading && !detail.data ? (
                  <LoadingState label="Загружаем задачу" />
                ) : (
                  <>
                    {detail.data ? <>
                      <nav className="rp-work-origin" aria-label="Возврат к главному блокеру">
                        <Link to={rootHref}>К главному блокеру {rootIssue}</Link>
                      </nav>
                      <Tabs ariaLabel="Разделы задачи" value={activeTab}
                        items={[{ id: 'task', label: 'Задача' }, { id: 'open', label: 'Открытые задачи' }, { id: 'closed', label: 'Закрытые задачи' }, { id: 'check', label: 'Проверка робота' }]}
                        onChange={tab => changeTab(tab as 'task' | 'open' | 'closed' | 'check')}
                        panelIdFor={tab => `work-panel-${tab}`} />
                    </> : null}
                    <TabPanel id="work-panel-task" labelledBy="tab-task" active={activeTab === 'task'} key={issueKey}>
                    <SyncStatus updatedAt={detail.updatedAt !== null && comments.updatedAt !== null ? Math.min(detail.updatedAt, comments.updatedAt) : null}
                      isRevalidating={detail.isRevalidating || comments.isRevalidating}
                      error={detail.error || comments.error} />
                    <IssueDetailPanel
                      currentUser={user.tracker_login ?? user.username} accountKey={user.username}
                      commentsLoading={comments.isLoading && !comments.data}
                      comments={comments.data ?? []} issue={detail.data ?? null}
                      loading={detail.isLoading && !detail.data} showRobotCheck={false}
                      onOpenRobotCheck={() => changeTab('check')}
                    />
                    {canRenderDetailActions && detail.data ? (
                      <IssueActionsPanel
                        capabilities={detail.data.capabilities}
                        draftOwner={user.username}
                        currentUser={user.tracker_login ?? user.username}
                        issueKey={detail.data.key}
                        issueUrl={detail.data.url}
                        onAssign={(assignee) => mutate(
                          () => apiClient.trackerAssign(detail.data!.key, assignee),
                        )}
                        onAttach={(file) => mutate(
                          () => apiClient.trackerAttach(detail.data!.key, file),
                        )}
                        onClose={() => mutate(() => apiClient.trackerClose(detail.data!.key), onCloseIssue)}
                        onComment={(text) => mutate(
                          () => apiClient.trackerComment(detail.data!.key, text),
                        )}
                        onTransition={(transition) => mutate(
                          () => apiClient.trackerTransition(detail.data!.key, transition),
                        )}
                        onUnassign={() => mutate(
                          () => apiClient.trackerUnassign(detail.data!.key),
                        )}
                        transitions={transitions.data ?? []}
                      />
                    ) : null}
                    </TabPanel>
                    {(['open', 'closed'] as const).map(kind => <TabPanel key={kind} id={`work-panel-${kind}`} labelledBy={`tab-${kind}`} active={activeTab === kind}>
                      {activeTab === kind && detail.data ? robotNumber && relatedPrefix && relatedQueue ? <RelatedTasksPanel
                        apiClient={apiClient} issueKey={issueKey ?? ''} kind={kind}
                        key={`${relatedPrefix}:${relatedRefreshGeneration}:${kind}`}
                        onOpen={onOpenRelatedIssue ?? saveAndOpenIssue} park={relatedPark}
                        resourcePrefix={relatedPrefix} robotNumber={robotNumber} queue={relatedQueue}
                      /> : <p>Робот в задаче не указан — связанные задачи недоступны.</p> : null}
                    </TabPanel>)}
                    <TabPanel id="work-panel-check" labelledBy="tab-check" active={activeTab === 'check'}>
                      {activeTab === 'check' && detail.data ? robotNumber ? <WorkRobotCheck
                        key={relatedPrefix} robot={robotNumber} user={user} activeTab={state.checkTab}
                        onAuthorizationFailure={failure => {
                          if (failure.kind === 'unauthorized') observeAuthorizationFailure(new ApiError(401, null, failure.requestId))
                        }}
                        onOpenTasks={() => changeTab('open')}
                        onTabChange={checkTab => onStateChange({ ...state, checkTab }, { replace: true })}
                      /> : <p>Робот в задаче не указан — проверка недоступна.</p> : null}
                    </TabPanel>
                  </>
                )}
              </ResourceBoundary>
            )}
          </div>}
          detailOpen={Boolean(issueKey)}
          list={<div className="rp-work-list-pane">
            <h2>Очередь задач</h2>
            <SyncStatus {...list} />
            <ResourceBoundary
              dataAvailable={list.data !== undefined}
              failure={listFailure}
              onRetry={() => void list.refresh()}
            >
              {list.isLoading && !list.data ? (
                <LoadingState label="Загружаем очередь задач" />
              ) : !listFailure && list.data?.items.length === 0 ? (
                <EmptyState
                  description="Измените фильтры или проверьте выбранный парк."
                  icon="work"
                  title="Нет задач"
                />
              ) : list.data ? (
                <div className="rp-work-list-scroll" ref={listScrollRef}>
                  <p className="rp-work-list-count">Показано {list.data.items.length}{list.data.total > list.data.items.length ? ` из ${list.data.total}` : ''}</p>
                  <WorkIssueRows
                    items={oldestFirst(list.data.items)}
                    onOpen={saveAndOpenIssue}
                    selected={issueKey}
                  />
                </div>
              ) : null}

              {list.data ? (
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
    return () => {
      mounted.current = false
      // Release synchronously: even an immediate new mount must not paint this
      // owner's protected cache. StrictMode replay safely starts a fresh read.
      currentAccess.current.generation += 1
      resourceStore.invalidate(committedAccessPrefix.current, { prefix: true })
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
    <IssueWorkbenchOwner
      {...props}
      apiClient={apiClient}
      accessKey={accessKey}
      getAccessGeneration={getAccessGeneration}
      key={ownerKey}
    />
  )
}
