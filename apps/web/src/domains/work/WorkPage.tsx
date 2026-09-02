import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth-context'
import {
  EmptyState,
  LoadingState,
} from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { useParkScope } from '../../app/park/parkScope'
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
  const { user, refreshUser } = useAuth()
  const { issueKey } = useParams<{ issueKey?: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { parkId, selectedPark, loading } = useParkScope()

  if (!user) return null
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

  const state = parseWorkUrl(params, { queue })
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
      description={`Парк: ${selectedPark.name} · очередь ${queue}`}
      title="Работа"
    >
      <IssueWorkbench
        apiClient={apiClient}
        issueKey={issueKey}
        onAuthorizationFailure={refreshUser}
        onCloseIssue={() => navigate(workListHref(state, parkId))}
        onOpenIssue={(key) => navigate(workIssueHref(key, state, parkId))}
        onStateChange={writeState}
        selectedPark={selectedPark}
        state={state}
        user={user}
      />
    </PageLayout>
  )
}
