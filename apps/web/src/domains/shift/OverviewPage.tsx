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
import { canReadOperations, operationsAccessIdentity, operationsSearch, parseOperationsQuery, type OperationsApiClient, type OperationsQuery } from '../insights/operations'
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

const retainable = new Set<DomainError['kind']>(['offline', 'timeout', 'server'])

function canSelectOverviewStatus(role: string): boolean {
  return role === 'operator' || role === 'admin' || role === 'royal'
}

function OverviewWarning({ failure, busy, onRetry }: { failure: DomainError; busy: boolean; onRetry: () => void }) {
  return <div className="rp-overview-warning" role="alert"><div><strong>{failure.title}</strong><p>{failure.description}</p></div>
    {failure.retryable ? <Button busy={busy} leadingIcon="refresh" onClick={onRetry} variant="secondary">Повторить</Button> : null}</div>
}

function OverviewContent({ data, role, selectable, statusHref, allHref }: { data: OperationsOverview; role: string; selectable: boolean; statusHref: (status: string) => string; allHref: string | null }) {
  const model = buildOverviewModel(data, role)

  return <div className="rp-overview">
    <OverviewAlerts alerts={model.alerts} />
    <OverviewStatusMonitoring allHref={allHref} selectable={selectable} statusCards={model.statusCards} statusHref={statusHref} />
    <OverviewFlow flow={model.flow} />
    <OverviewAttentionQueue attentionQueue={model.attentionQueue} attentionTruncated={model.attentionTruncated} />
    <OverviewWorkload workload={model.workload} />
    <OverviewOperatorAccounts operatorAccounts={model.operatorAccounts} />
  </div>
}

function OverviewResource({ resourceKey, load, parkId, role, selectable, statusHref, allHref, onAuthorizationFailure }: {
  resourceKey: string
  load: () => Promise<OperationsOverview>
  parkId: number
  role: string
  selectable: boolean
  statusHref: (status: string) => string
  allHref: string | null
  onAuthorizationFailure: (error: unknown) => void
}) {
  const resource = useCachedResource(resourceKey, load, { persist: false })
  useLayoutEffect(() => () => resourceStore.invalidate(resourceKey), [resourceKey])
  useEffect(() => { if (resource.error) onAuthorizationFailure(resource.error) }, [onAuthorizationFailure, resource.error])
  const data = resource.data?.park_id === parkId ? resource.data : undefined
  const failure = resource.error ? classifyApiError(resource.error, 'Не удалось загрузить обзор смены.') : null

  if (failure?.kind === 'unauthorized' || failure?.kind === 'forbidden') return null
  if (failure && !(data && retainable.has(failure.kind))) return <ErrorState description={failure.description} onRetry={failure.retryable ? () => void resource.refresh() : undefined} requestId={failure.requestId} title={failure.title} />
  if (!data) return <LoadingState label="Загружаем обзор смены" variant="page" />

  return <>
    {failure ? <OverviewWarning busy={resource.isRevalidating} failure={failure} onRetry={() => void resource.refresh()} /> : null}
    <SyncStatus {...resource} />
    <OverviewContent allHref={allHref} data={data} role={role} selectable={selectable} statusHref={statusHref} />
  </>
}

function OverviewBoundary({ apiClient, user, query, selectable, statusHref, allHref, authorizationBlocked, onAuthorizationFailure }: {
  apiClient: OperationsApiClient
  user: User
  query: OperationsQuery
  selectable: boolean
  statusHref: (status: string) => string
  allHref: string | null
  authorizationBlocked: () => boolean
  onAuthorizationFailure: (error: unknown) => void
}) {
  const { selectedPark } = useParkScope()
  const cachePrefix = `overview:${user.id}:`
  const identity = operationsAccessIdentity(user, selectedPark)
  const resourceKey = selectedPark ? `${cachePrefix}${identity}:${selectedPark.id}:${query.days}:${query.status}` : `${cachePrefix}${identity}:none`
  const load = useCallback(async () => {
    if (authorizationBlocked()) throw new Error('overview_authorization_blocked')
    const result = await apiClient.operationsOverview(selectedPark!.id, query.days, query.status)
    if (authorizationBlocked()) throw new Error('overview_authorization_blocked')
    return result
  }, [apiClient, authorizationBlocked, query.days, query.status, selectedPark])

  if (!canReadOperations(user, 'overview')) return <ErrorState description="Для этого раздела нужны доступ к Tracker и разрешение на обзор смены." title="Нет доступа" />
  if (!selectedPark) return <EmptyState description="Выберите доступный парк, чтобы увидеть текущие задачи." icon="parks" title="Парк не выбран" />
  return <OverviewResource allHref={allHref} key={resourceKey} load={load} onAuthorizationFailure={onAuthorizationFailure} parkId={selectedPark.id} resourceKey={resourceKey} role={user.role} selectable={selectable} statusHref={statusHref} />
}

function OverviewSessionPage({ apiClient, user }: { apiClient: OperationsApiClient; user: User }) {
  const { refreshUser } = useAuth()
  const { loading, selectedPark } = useParkScope()
  const [params, setParams] = useSearchParams()
  const parsed = useMemo(() => parseOperationsQuery(params, user.role), [params, user.role])
  const selectable = canSelectOverviewStatus(user.role)
  const query = useMemo<OperationsQuery>(() => ({ days: 7, status: selectable ? parsed.status : 'all' }), [parsed.status, selectable])
  const normalized = useMemo(() => operationsSearch(params, query), [params, query])
  const cachePrefix = `overview:${user.id}:`
  const identity = `${operationsAccessIdentity(user, selectedPark)}:${query.days}:${query.status}`
  const requestOwner = useMemo(() => ({ identity }), [identity])
  const owner = useRef<typeof requestOwner | null>(requestOwner)
  useLayoutEffect(() => { owner.current = requestOwner; return () => { owner.current = null } }, [requestOwner])
  const blocked = useRef<{ unauthorized: boolean; forbidden: string | null }>({ unauthorized: false, forbidden: null })
  const refreshStarted = useRef(new Set<string>())
  const [authorizationFailure, setAuthorizationFailure] = useState<{ identity: string; failure: DomainError } | null>(null)

  const observeAuthorizationFailure = useCallback((error: unknown) => {
    if (owner.current !== requestOwner) return
    const failure = classifyApiError(error, 'Не удалось загрузить обзор смены.')
    if (failure.kind !== 'unauthorized' && failure.kind !== 'forbidden') return
    if (failure.kind === 'unauthorized') blocked.current.unauthorized = true
    else blocked.current.forbidden = identity
    resourceStore.invalidate(cachePrefix, { prefix: true })
    setAuthorizationFailure({ identity, failure })
    const refreshKey = failure.kind === 'unauthorized' ? 'session' : identity
    if (!refreshStarted.current.has(refreshKey)) {
      refreshStarted.current.add(refreshKey)
      void refreshUser().catch(() => undefined)
    }
  }, [cachePrefix, identity, refreshUser, requestOwner])
  const authorizationBlocked = useCallback(() => owner.current !== requestOwner || blocked.current.unauthorized || blocked.current.forbidden === identity, [identity, requestOwner])
  const contextFailure = authorizationFailure?.failure.kind === 'unauthorized' || authorizationFailure?.identity === identity ? authorizationFailure.failure : null

  useLayoutEffect(() => {
    if (normalized.toString() !== params.toString()) setParams(normalized, { replace: true })
  }, [normalized, params, setParams])

  const statusHref = useCallback((nextStatus: string) => {
    const next = operationsSearch(params, { days: 7, status: nextStatus })
    return next.toString() ? `?${next.toString()}` : ''
  }, [params])
  const allHref = selectable && query.status !== 'all' ? statusHref('all') : null

  return <PageLayout description="Что происходит сейчас и где требуется вмешательство в выбранном парке." title="Смена / Обзор">
    {contextFailure ? <ErrorState description={contextFailure.description} requestId={contextFailure.requestId} title={contextFailure.title} />
      : loading ? <LoadingState label="Загружаем область парка" variant="page" />
        : <OverviewBoundary allHref={allHref} apiClient={apiClient} authorizationBlocked={authorizationBlocked} onAuthorizationFailure={observeAuthorizationFailure} query={query} selectable={selectable} statusHref={statusHref} user={user} />}
  </PageLayout>
}

export function OverviewPage({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  const { user } = useAuth()
  return user ? <OverviewSessionPage key={user.id} apiClient={apiClient} user={user} /> : null
}
