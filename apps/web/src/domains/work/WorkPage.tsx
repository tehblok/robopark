import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type DashboardSummary, type User } from '../../api'
import { useAuth } from '../../auth-context'
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { useParkScope } from '../../app/park/parkScope'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { useCachedResource } from '../../lib/resource'
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
  apiClient?: IssueWorkbenchApiClient & Pick<typeof api, 'dashboardSummary'>
}) {
  const { user } = useAuth()
  if (!user) return null

  return <WorkPageOwner apiClient={apiClient} key={user.id} user={user} />
}

function WorkPageOwner({
  apiClient,
  user,
}: {
  apiClient: IssueWorkbenchApiClient & Pick<typeof api, 'dashboardSummary'>
  user: User
}) {
  const { refreshUser } = useAuth()
  const { issueKey } = useParams<{ issueKey?: string }>()
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { parkId, selectedPark, loading } = useParkScope()
  const refreshStarted = useRef(false)
  const [now, setNow] = useState(() => Date.now())
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const [summaryOpen, setSummaryOpen] = useState(false)
  const summary = useCachedResource<DashboardSummary>(
    `dashboard:summary:${parkId ?? 'none'}`,
    () => apiClient.dashboardSummary(parkId as number),
    { enabled: summaryOpen && parkId != null },
  )
  useEffect(() => {
    if (user.role !== 'driver') return
    const status = params.get('status')
    if (!status || status === 'new' || status === 'moving') return
    const normalized = new URLSearchParams(params)
    normalized.set('status', 'new')
    navigate({ pathname: location.pathname, search: normalized.toString() }, { replace: true })
  }, [location.pathname, navigate, params, user.role])
  useEffect(() => {
    let timer = 0
    const schedule = () => {
      const delay = 60_000 - (Date.now() % 60_000)
      timer = window.setTimeout(() => {
        setNow(Date.now())
        schedule()
      }, delay)
    }
    schedule()
    return () => window.clearTimeout(timer)
  }, [])
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

  const parsedState = parseWorkUrl(params, {
    queue,
    status: user.role === 'driver' ? 'new' : 'queued',
  })
  const state = user.role === 'driver' && parsedState.filters.status !== 'new' && parsedState.filters.status !== 'moving'
    ? { ...parsedState, filters: { ...parsedState.filters, status: 'new' } }
    : parsedState
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
      className={issueKey ? 'rp-work-page--detail' : undefined}
      description={`Парк: ${selectedPark.name} · открытые блокеры`}
      title="Работа"
    >
      <section className="rp-work-summary">
        <button aria-expanded={summaryOpen} onClick={() => setSummaryOpen(open => !open)} type="button">Сводка смены</button>
        {summaryOpen ? <div className="rp-work-summary__content">
          {summary.error ? <p role="alert">Не удалось загрузить сводку.</p> : null}
          {!summary.data && summary.isLoading ? <p>Загружаем сводку…</p> : null}
          {summary.data ? <div className="rp-work-summary__metrics">
            <span>Пришли: {summary.data.arrived}</span>
            <span>Ушли: {summary.data.done}</span>
            <span>В очереди: {summary.data.queued}</span>
            <span>В пути: {summary.data.in_transit}</span>
          </div> : null}
        </div> : null}
      </section>
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
        now={now}
        selectedPark={selectedPark}
        state={state}
        user={user}
      />
    </PageLayout>
  )
}
