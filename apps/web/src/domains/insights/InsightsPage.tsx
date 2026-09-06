import { SyncStatus } from '../../design-system/status/SyncStatus'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type OperationsOverview, type User } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { FlowChart } from './FlowChart'
import { OperationsLeadership, OperationsMetrics, OperationsSla, OperationsTasks, OperationsUpdated } from './OperationsPanels'
import { allowedStatuses, canReadOperations, operationsAccessIdentity, operationsSearch, parseOperationsQuery, STATUS_LABELS, type OperationsApiClient, type OperationsQuery } from './operations'
import './insights.css'

const retainable = new Set<DomainError['kind']>(['offline', 'timeout', 'server'])

type InsightsMode = 'overview' | 'analytics'
export type InsightsPageProps = { apiClient?: OperationsApiClient; mode: InsightsMode }

function OperationsWarning({ failure, busy, onRetry }: { failure: DomainError; busy: boolean; onRetry: () => void }) {
  return <div className="rp-insights-warning" role="alert"><div><strong>{failure.title}</strong><p>{failure.description}</p></div>
    {failure.retryable ? <Button busy={busy} leadingIcon="refresh" onClick={onRetry} variant="secondary">Повторить</Button> : null}</div>
}

function OperationsContent({ data }: { data: OperationsOverview }) {
  return <div className="rp-insights">
    <OperationsUpdated data={data} />
    <OperationsMetrics data={data} />
    <div className="rp-insights-grid"><OperationsTasks data={data} /><OperationsSla data={data} /></div>
    <FlowChart flow={data.flow} />
    <div className="rp-insights-grid"><OperationsLeadership data={data} /></div>
  </div>
}

function OperationsOwner({ resourceKey, load, parkId, onAuthorizationFailure }: {
  resourceKey: string
  load: () => Promise<OperationsOverview>
  parkId: number
  onAuthorizationFailure: (error: unknown) => void
}) {
  const resource = useCachedResource(resourceKey, load, { persist: false })
  useLayoutEffect(() => () => resourceStore.invalidate(resourceKey), [resourceKey])
  useEffect(() => { if (resource.error) onAuthorizationFailure(resource.error) }, [onAuthorizationFailure, resource.error])
  const data = resource.data?.park_id === parkId ? resource.data : undefined
  const failure = resource.error ? classifyApiError(resource.error, 'Не удалось загрузить операционный обзор.') : null
  if (failure?.kind === 'unauthorized' || failure?.kind === 'forbidden') return null
  if (failure && !(data && retainable.has(failure.kind))) {
    return <ErrorState title={failure.title} description={failure.description} requestId={failure.requestId} onRetry={failure.retryable ? () => void resource.refresh() : undefined} />
  }
  if (!data) return <LoadingState label="Загружаем операционный обзор" variant="page" />
  return <>{failure ? <OperationsWarning failure={failure} busy={resource.isRevalidating} onRetry={() => void resource.refresh()} /> : null}
    <SyncStatus {...resource} />
    <OperationsContent data={data} />
  </>
}

function InsightsBoundary({ apiClient, mode, user, query, authorizationBlocked, onAuthorizationFailure }: {
  apiClient: OperationsApiClient
  mode: InsightsMode
  user: User
  query: OperationsQuery
  authorizationBlocked: () => boolean
  onAuthorizationFailure: (error: unknown) => void
}) {
  const { selectedPark } = useParkScope()
  const cachePrefix = `operations:${user.id}:`
  const identity = operationsAccessIdentity(user, selectedPark)
  const resourceKey = selectedPark ? `${cachePrefix}${identity}:${selectedPark.id}:${query.days}:${query.status}` : `${cachePrefix}${identity}:none`

  const load = useCallback(async () => {
    if (authorizationBlocked()) throw new Error('operations_authorization_blocked')
    const result = await apiClient.operationsOverview(selectedPark!.id, query.days, query.status)
    if (authorizationBlocked()) throw new Error('operations_authorization_blocked')
    return result
  }, [apiClient, authorizationBlocked, query.days, query.status, selectedPark])

  if (!canReadOperations(user, mode)) return <ErrorState title="Нет доступа" description="Для этого раздела нужны доступ к Tracker и разрешение на раздел." />
  if (!selectedPark) return <EmptyState title="Парк не выбран" description="Выберите доступный парк, чтобы увидеть задачи и SLA." icon="parks" />
  return <OperationsOwner key={resourceKey} resourceKey={resourceKey} load={load} parkId={selectedPark.id} onAuthorizationFailure={onAuthorizationFailure} />
}

function InsightsSessionPage({ apiClient, mode, user }: { apiClient: OperationsApiClient; mode: InsightsMode; user: User }) {
  const { refreshUser } = useAuth()
  const { selectedPark, loading } = useParkScope()
  const [params, setParams] = useSearchParams()
  const query = useMemo(() => parseOperationsQuery(params, user.role), [params, user.role])
  const normalized = useMemo(() => operationsSearch(params, query), [params, query])
  const cachePrefix = `operations:${user.id}:`
  const blocked = useRef(false)
  const refreshStarted = useRef(false)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)

  const observeAuthorizationFailure = useCallback((error: unknown) => {
    const failure = classifyApiError(error, 'Не удалось загрузить операционный обзор.')
    if (failure.kind !== 'unauthorized' && failure.kind !== 'forbidden') return
    blocked.current = true
    resourceStore.invalidate(cachePrefix, { prefix: true })
    setAuthorizationFailure(failure)
    if (!refreshStarted.current) {
      refreshStarted.current = true
      void refreshUser().catch(() => undefined)
    }
  }, [cachePrefix, refreshUser])
  const authorizationBlocked = useCallback(() => blocked.current, [])

  useLayoutEffect(() => {
    if (normalized.toString() !== params.toString()) setParams(normalized, { replace: true })
  }, [normalized, params, setParams])

  const update = (next: OperationsQuery) => setParams(operationsSearch(params, next), { replace: true })
  const accessIdentity = operationsAccessIdentity(user, selectedPark)
  return <PageLayout title={mode === 'overview' ? 'Смена / Обзор' : 'Аналитика'} description={mode === 'overview' ? 'Текущие задачи, поток и SLA выбранного парка.' : 'Один операционный срез по выбранному парку.'}>
    <div className="rp-insights-controls">
      <label>Период<select aria-label="Период" value={query.days} onChange={(event) => update({ ...query, days: Number(event.target.value) })}><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>
      <label>Статус задач<select aria-label="Статус задач" value={query.status} onChange={(event) => update({ ...query, status: event.target.value })}>{allowedStatuses(user.role).map((status) => <option value={status} key={status}>{STATUS_LABELS[status]}</option>)}</select></label>
    </div>
    {authorizationFailure ? <ErrorState title={authorizationFailure.title} description={authorizationFailure.description} requestId={authorizationFailure.requestId} />
      : loading ? <LoadingState label="Загружаем область парка" variant="page" />
        : <InsightsBoundary key={accessIdentity} apiClient={apiClient} mode={mode} user={user} query={query} authorizationBlocked={authorizationBlocked} onAuthorizationFailure={observeAuthorizationFailure} />}
  </PageLayout>
}

export function InsightsPage({ apiClient = api, mode }: InsightsPageProps) {
  const { user } = useAuth()
  return user ? <InsightsSessionPage key={user.id} apiClient={apiClient} mode={mode} user={user} /> : null
}
