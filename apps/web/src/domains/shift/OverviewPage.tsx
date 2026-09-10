import { SyncStatus } from '../../design-system/status/SyncStatus'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type OperationsOverview, type Park, type User } from '../../api'
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
import { CampaignOverviewSection } from '../campaigns/CampaignsPage'
import { limitOperationsRequest } from './operationsRequestLimit'
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

function OverviewPark({ apiClient, user, park, query, selectable, params, authorizationBlocked, onAuthorizationFailure, identity }: {
  apiClient: OperationsApiClient
  user: User
  park: Park
  query: OperationsQuery
  selectable: boolean
  params: URLSearchParams
  identity: string
  authorizationBlocked: () => boolean
  onAuthorizationFailure: (error: unknown) => void
}) {
  const resourceKey = `overview:${user.id}:${identity}:${park.id}`
  const load = useCallback(() => limitOperationsRequest(async () => {
    if (authorizationBlocked()) throw new Error('overview_authorization_blocked')
    try {
      const result = await apiClient.operationsOverview(park.id, query.days, query.status)
      if (authorizationBlocked()) throw new Error('overview_authorization_blocked')
      return result
    } catch (error) {
      onAuthorizationFailure(error)
      throw error
    }
  }), [apiClient, authorizationBlocked, onAuthorizationFailure, park.id, query.days, query.status])
  const statusHref = (nextStatus: string) => {
    const next = operationsSearch(params, { days: 7, status: nextStatus })
    next.set('park', String(park.id))
    return `?${next.toString()}`
  }
  const allHref = selectable && query.status !== 'all' ? statusHref('all') : null
  return <OverviewResource allHref={allHref} key={resourceKey} load={load} onAuthorizationFailure={onAuthorizationFailure} parkId={park.id} resourceKey={resourceKey} role={user.role} selectable={selectable} statusHref={statusHref} />
}

function OverviewSessionPage({ apiClient, user }: { apiClient: OperationsApiClient; user: User }) {
  const { refreshUser } = useAuth()
  const { loading, selectedPark, parks } = useParkScope()
  const allParks = !selectedPark && canSelectOverviewStatus(user.role)
  const accessible = (park: Park) => park.is_active !== false && (user.role === 'admin' || user.role === 'royal' || user.parks.some(assigned => assigned.id === park.id && assigned.is_active !== false))
  const accessibleParks = parks.filter(accessible)
  const overviewParks = selectedPark ? [selectedPark].filter(accessible) : allParks ? accessibleParks : []
  const [params, setParams] = useSearchParams()
  const parsed = useMemo(() => parseOperationsQuery(params, user.role), [params, user.role])
  const selectable = canSelectOverviewStatus(user.role)
  const query = useMemo<OperationsQuery>(() => ({ days: 7, status: selectable ? parsed.status : 'all' }), [parsed.status, selectable])
  const normalized = useMemo(() => operationsSearch(params, query), [params, query])
  const cachePrefix = `overview:${user.id}:`
  const identity = `${operationsAccessIdentity(user, selectedPark)}:${JSON.stringify(overviewParks.map(park => operationsAccessIdentity(user, park)))}:${query.days}:${query.status}`
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


  return <PageLayout description={allParks ? "Что происходит сейчас во всех доступных парках. Задачи, SLA и история показаны отдельно по каждому парку." : "Что происходит сейчас и где требуется вмешательство в выбранном парке."} title="Смена / Обзор">
    {contextFailure ? <ErrorState description={contextFailure.description} requestId={contextFailure.requestId} title={contextFailure.title} />
      : loading ? <LoadingState label="Загружаем область парка" variant="page" />
        : !canReadOperations(user, 'overview') ? <ErrorState description="Для этого раздела нужны доступ к Tracker и разрешение на обзор смены." title="Нет доступа" />
          : overviewParks.length === 0 ? <EmptyState description="Нет доступных парков для обзора смены." icon="parks" title="Парк не выбран" />
            : overviewParks.map(park => {
              const content = <><OverviewPark apiClient={apiClient} authorizationBlocked={authorizationBlocked} identity={identity} onAuthorizationFailure={observeAuthorizationFailure} params={params} park={park} query={query} selectable={selectable} user={user} /><CampaignOverviewSection parkId={park.id} /></>
              return allParks ? <section key={park.id} aria-label={park.name}><h2>{park.name}</h2>{content}</section> : content
            })}
  </PageLayout>
}

export function OverviewPage({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  const { user } = useAuth()
  return user ? <OverviewSessionPage key={user.id} apiClient={apiClient} user={user} /> : null
}
