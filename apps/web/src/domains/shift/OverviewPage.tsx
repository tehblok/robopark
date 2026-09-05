import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type OperationsOverview } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { useCachedResource } from '../../lib/resource'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { canReadOperations, type OperationsApiClient } from '../insights/operations'
import {
  OverviewAlerts,
  OverviewAttentionQueue,
  OverviewFlow,
  OverviewOperatorAccounts,
  OverviewStatusMonitoring,
  OverviewWorkload,
} from './OverviewSections'
import { buildOverviewModel } from './overviewModel'
import './overview.css'

function OverviewContent({ data, role, statusHref }: { data: OperationsOverview; role: string; statusHref: (status: string) => string }) {
  const model = buildOverviewModel(data, role)

  return <div className="rp-overview">
    <OverviewAlerts alerts={model.alerts} />
    <OverviewStatusMonitoring statusCards={model.statusCards} statusHref={statusHref} />
    <OverviewFlow flow={model.flow} />
    <OverviewAttentionQueue attentionQueue={model.attentionQueue} attentionTruncated={model.attentionTruncated} />
    <OverviewWorkload workload={model.workload} />
    <OverviewOperatorAccounts operatorAccounts={model.operatorAccounts} />
  </div>
}

function OverviewResource({ apiClient, role, status, statusHref }: { apiClient: OperationsApiClient; role: string; status: string; statusHref: (status: string) => string }) {
  const { selectedPark } = useParkScope()
  const resourceKey = selectedPark ? `overview:${selectedPark.id}:${role}:${status}` : 'overview:none'
  const load = useCallback(() => apiClient.operationsOverview(selectedPark!.id, 7, status), [apiClient, selectedPark, status])
  const resource = useCachedResource(resourceKey, load, { persist: false })
  const data = resource.data?.park_id === selectedPark?.id ? resource.data : undefined

  if (!data && resource.error) {
    const failure = classifyApiError(resource.error, 'Не удалось загрузить обзор смены.')
    return <ErrorState description={failure.description} onRetry={failure.retryable ? () => void resource.refresh() : undefined} requestId={failure.requestId} title={failure.title} />
  }
  if (!data) return <LoadingState label="Загружаем обзор смены" variant="page" />

  return <>
    <OverviewContent data={data} role={role} statusHref={statusHref} />
    <Button busy={resource.isRevalidating} leadingIcon="refresh" onClick={() => void resource.refresh()} variant="secondary">Обновить данные</Button>
  </>
}

function OverviewSessionPage({ apiClient }: { apiClient: OperationsApiClient }) {
  const { user } = useAuth()
  const { selectedPark, loading } = useParkScope()
  const [params] = useSearchParams()
  const status = params.get('status') ?? 'all'
  const statusHref = useCallback((nextStatus: string) => {
    const next = new URLSearchParams(params)
    next.set('status', nextStatus)
    return `?${next.toString()}`
  }, [params])

  if (!user) return null
  if (!canReadOperations(user, 'overview')) return <PageLayout description="Для этого раздела нужны доступ к Tracker и разрешение на обзор." title="Смена / Обзор"><ErrorState description="Обратитесь к администратору за доступом к обзору смены." title="Нет доступа" /></PageLayout>

  return <PageLayout description="Что происходит сейчас и где требуется вмешательство в выбранном парке." title="Смена / Обзор">
    {loading ? <LoadingState label="Загружаем область парка" variant="page" />
      : !selectedPark ? <EmptyState description="Выберите доступный парк, чтобы увидеть текущие задачи." icon="parks" title="Парк не выбран" />
        : <OverviewResource apiClient={apiClient} role={user.role} status={status} statusHref={statusHref} />}
  </PageLayout>
}

export function OverviewPage({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  return <OverviewSessionPage apiClient={apiClient} />
}
