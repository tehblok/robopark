import {
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  api,
  type Park,
  type TrackerIssueDetail,
  type User,
} from '../../api'
import { IssueActionsPanel } from '../../components/tracker/IssueActionsPanel'
import { IssueDetailPanel } from '../../components/tracker/IssueDetailPanel'
import { IssueList } from '../../components/tracker/IssueList'
import { Button } from '../../design-system/actions/Button'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import {
  classifyApiError,
  type DomainError,
} from '../../shared/api/classifyApiError'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { WorkFilters } from './WorkFilters'
import { loadWorkPage } from './workData'
import {
  buildWorkSearch,
  readWorkScroll,
  saveWorkScroll,
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
  onOpenIssue(key: string): void
  onCloseIssue(): void
  onAuthorizationFailure(): Promise<unknown>
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
  onCloseIssue,
  onAuthorizationFailure,
}: Required<Pick<IssueWorkbenchProps, 'apiClient'>> & Omit<IssueWorkbenchProps, 'apiClient'>) {
  const cachePrefix = `work:${user.id}:`
  const allowUntagged = user.role === 'operator'
    || user.role === 'admin'
    || user.role === 'royal'
  const requestState = useMemo<WorkUrlState>(() => {
    if (allowUntagged || !state.filters.untagged) return state
    const filters = { ...state.filters }
    delete filters.untagged
    return { ...state, filters }
  }, [allowUntagged, state])
  const listKey = `${cachePrefix}list:${selectedPark.id}:${JSON.stringify(requestState)}`
  const detailKey = issueKey ? `${cachePrefix}issue:${issueKey}` : ''
  const commentsKey = issueKey ? `${cachePrefix}comments:${issueKey}` : ''
  const blockedRef = useRef(false)
  const blockedErrorRef = useRef<unknown>(null)
  const refreshStartedRef = useRef(false)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)

  const observeAuthorizationFailure = useCallback((error: unknown) => {
    const failure = classifyApiError(error, 'Не удалось загрузить рабочие данные.')
    if (!isAuthorizationFailure(failure)) return

    blockedRef.current = true
    blockedErrorRef.current = error
    resourceStore.invalidate(cachePrefix, { prefix: true })
    setAuthorizationFailure(failure)
    if (!refreshStartedRef.current) {
      refreshStartedRef.current = true
      void onAuthorizationFailure().catch(() => undefined)
    }
  }, [cachePrefix, onAuthorizationFailure])

  const guarded = useCallback(async <T,>(loader: () => Promise<T>): Promise<T> => {
    if (blockedRef.current) {
      throw blockedErrorRef.current ?? new Error('work_authorization_blocked')
    }
    try {
      const value = await loader()
      if (blockedRef.current) {
        throw blockedErrorRef.current ?? new Error('work_authorization_blocked')
      }
      return value
    } catch (error) {
      observeAuthorizationFailure(error)
      throw error
    }
  }, [observeAuthorizationFailure])

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
  const transitionsEnabled = Boolean(
    issueKey && detail.data?.capabilities.transition,
  )
  const transitionsKey = transitionsEnabled
    ? `${cachePrefix}transitions:${issueKey}`
    : ''
  const transitions = useCachedResource(
    transitionsKey,
    () => guarded(() => apiClient.trackerTransitions(issueKey as string)),
    { enabled: transitionsEnabled },
  )

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
    resourceStore.invalidate(`${cachePrefix}transitions:${issueKey}`)
  }, [cachePrefix, commentsKey, detailFailure?.kind, detailKey, issueKey])

  const search = buildWorkSearch(state, selectedPark.id)
  const listDataAvailable = list.data !== undefined
  const listScrollRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = listScrollRef.current
    if (!element) return undefined
    element.scrollTop = readWorkScroll(user.id, search)
    return () => {
      saveWorkScroll(user.id, search, element.scrollTop)
    }
  }, [listDataAvailable, search, user.id])

  const saveAndOpenIssue = (key: string) => {
    saveWorkScroll(user.id, search, listScrollRef.current?.scrollTop ?? 0)
    onOpenIssue(key)
  }

  const invalidateMutationResources = useCallback(() => {
    resourceStore.invalidate(`${cachePrefix}list:${selectedPark.id}:`, {
      prefix: true,
    })
    if (!issueKey) return
    resourceStore.invalidate(detailKey)
    resourceStore.invalidate(commentsKey)
    resourceStore.invalidate(`${cachePrefix}transitions:${issueKey}`)
    void Promise.allSettled([
      list.refresh(),
      detail.refresh(),
      comments.refresh(),
      ...(transitionsEnabled ? [transitions.refresh()] : []),
    ])
  }, [
    cachePrefix,
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

  const mutate = useCallback(async (action: () => Promise<unknown>) => {
    await guarded(action)
    invalidateMutationResources()
  }, [guarded, invalidateMutationResources])

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
        allowUntagged={allowUntagged}
        key={buildWorkSearch(state, null)}
        loading={list.isRevalidating}
        onApply={(next) => onStateChange(next, { replace: false })}
        trackerLogin={user.tracker_login}
        value={state}
      />

      <div
        className="rp-workbench"
        data-has-detail={Boolean(issueKey)}
      >
        <Panel className="rp-work-list-pane" title="Очередь задач">
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
                <IssueList
                  items={list.data.items}
                  loading={list.isRevalidating}
                  onSelect={saveAndOpenIssue}
                  selected={issueKey ?? ''}
                  total={list.data.total}
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
        </Panel>

        <Panel
          className="rp-work-detail-pane"
          title={issueKey ? `Задача ${issueKey}` : 'Детали задачи'}
        >
          {issueKey ? (
            <Button
              className="rp-work-detail-back"
              onClick={onCloseIssue}
              variant="ghost"
            >
              Назад к списку
            </Button>
          ) : null}

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
                  <IssueDetailPanel
                    comments={comments.data ?? []}
                    issue={detail.data ?? null}
                    loading={detail.isLoading && !detail.data}
                  />
                  {canRenderDetailActions && detail.data ? (
                    <IssueActionsPanel
                      capabilities={detail.data.capabilities}
                      currentUser={user.tracker_login ?? user.username}
                      issueKey={detail.data.key}
                      issueUrl={detail.data.url}
                      onAssign={(assignee) => mutate(
                        () => apiClient.trackerAssign(detail.data!.key, assignee),
                      )}
                      onAttach={(file) => mutate(
                        () => apiClient.trackerAttach(detail.data!.key, file),
                      )}
                      onClose={async () => {
                        await mutate(() => apiClient.trackerClose(detail.data!.key))
                        onCloseIssue()
                      }}
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
                </>
              )}
            </ResourceBoundary>
          )}
        </Panel>
      </div>
    </div>
  )
}

export function IssueWorkbench({
  apiClient = api,
  ...props
}: IssueWorkbenchProps) {
  const ownerKey = [
    props.user.id,
    props.selectedPark.id,
    buildWorkSearch(props.state, null),
    props.issueKey ?? '',
  ].join(':')

  return (
    <IssueWorkbenchOwner
      {...props}
      apiClient={apiClient}
      key={ownerKey}
    />
  )
}
