import { useCallback, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type User } from '../../api'
import { useAuth } from '../../auth-context'
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { useParkScope } from '../../app/park/parkScope'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import {
  IssueWorkbench,
  type IssueWorkbenchApiClient,
} from './IssueWorkbench'
import {
  buildWorkSearch,
  parseWorkUrl,
  workIssueHref,
  workListHref,
  type WorkUrlState,
} from './workUrl'

export function WorkPage({
  apiClient = api,
}: {
  apiClient?: IssueWorkbenchApiClient
}) {
  const { user } = useAuth()
  if (!user) return null

  return <WorkPageOwner apiClient={apiClient} key={user.id} user={user} />
}

function WorkPageOwner({
  apiClient,
  user,
}: {
  apiClient: IssueWorkbenchApiClient
  user: User
}) {
  const { refreshUser } = useAuth()
  const { issueKey } = useParams<{ issueKey?: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { parkId, selectedPark, loading } = useParkScope()
  const refreshStarted = useRef(false)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const observeAuthorizationFailure = useCallback(async (error: unknown) => {
    if (refreshStarted.current) return
    refreshStarted.current = true
    setAuthorizationFailure(classifyApiError(error, 'Не удалось загрузить рабочие данные.'))
    return refreshUser()
  }, [refreshUser])

  // Park reselection may temporarily unmount the resource owner after /auth/me.
  // The denial belongs to the principal, not to that transient park state.
  if (authorizationFailure) {
    return (
      <ErrorState
        description={authorizationFailure.description}
        requestId={authorizationFailure.requestId}
        title={authorizationFailure.title}
      />
    )
  }
  if (loading) {
    return <LoadingState label="Загружаем область работы" variant="page" />
  }
  if (!selectedPark || parkId == null) {
    return (
      <EmptyState
        description="Выберите доступный парк, чтобы открыть очередь."
        icon="parks"
        title="Парк не выбран"
      />
    )
  }

  const queue = selectedPark.tracker_queue?.trim()
  if (!queue) {
    return (
      <EmptyState
        description={`Для парка «${selectedPark.name}» не указана очередь Tracker.`}
        icon="warning"
        title="Очередь не настроена"
      />
    )
  }

  const state = parseWorkUrl(params, { queue, status: user.role === 'driver' ? 'new' : 'queued' })
  const writeState = (
    next: WorkUrlState,
    options: { replace?: boolean } = {},
  ) => {
    navigate(
      {
        pathname: issueKey
          ? `/work/${encodeURIComponent(issueKey)}`
          : '/work',
        search: buildWorkSearch(next, parkId),
      },
      { replace: options.replace },
    )
  }

  return (
    <PageLayout
      description={`Парк: ${selectedPark.name} · открытые блокеры`}
      title="Работа"
    >
      <IssueWorkbench
        apiClient={apiClient}
        issueKey={issueKey}
        onAuthorizationFailure={observeAuthorizationFailure}
        onCloseIssue={() => navigate(state.rootIssue
          ? workIssueHref(state.rootIssue, { ...state, rootIssue: undefined, detailTab: undefined, checkTab: undefined }, parkId)
          : workListHref({ ...state, detailTab: undefined, checkTab: undefined }, parkId))}
        onOpenIssue={(key) => navigate(workIssueHref(key, { ...state, rootIssue: undefined, detailTab: undefined, checkTab: undefined }, parkId))}
        onOpenRelatedIssue={(key) => navigate(workIssueHref(key, { ...state, rootIssue: key === (state.rootIssue ?? issueKey) ? undefined : state.rootIssue ?? issueKey, detailTab: undefined, checkTab: undefined }, parkId))}
        onStateChange={writeState}
        selectedPark={selectedPark}
        state={state}
        user={user}
      />
    </PageLayout>
  )
}
