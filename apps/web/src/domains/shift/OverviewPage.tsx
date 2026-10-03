import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, type OperationsOverview, type Park, type User } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from '../../design-system/layout/ResponsiveDisclosure'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { canReadOperations, operationsAccessIdentity, operationsSearch, parseOperationsQuery, type OperationsApiClient, type OperationsQuery } from '../insights/operations'
import {
  OverviewAlerts,
  OverviewAttentionQueue,
  OverviewFlow,
  OverviewHeadline,
  OverviewOperatorAccounts,
  OverviewStatusMonitoring,
  OverviewWorkload,
} from './OverviewSections'
import { buildOverviewModel } from './overviewModel'
import { useMinuteClock } from '../../lib/useMinuteClock'
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
  const now = useMinuteClock()
  const model = buildOverviewModel(data, role, now)

  return <div className="rp-overview">
    <OverviewHeadline headline={model.headline} />
    <div className="rp-overview-primary" data-testid="overview-primary">
      <OverviewAttentionQueue attentionQueue={model.attentionQueue} attentionTruncated={model.attentionTruncated} fullQueueHref={model.fullQueueHref} timezone={model.timezone} />
      <OverviewAlerts alerts={model.alerts} />
    </div>
    <OverviewStatusMonitoring allHref={allHref} selectable={selectable} statusCards={model.statusCards} statusHref={statusHref} />
    <ResponsiveDisclosureGroup label="Вторичные показатели смены">
      <ResponsiveDisclosure id="overview-secondary" summary="Поток, нагрузка и учётные записи" title="Дополнительные показатели">
        <div className="rp-overview-secondary">
          <OverviewFlow flow={model.flow} />
          <OverviewWorkload workload={model.workload} />
          <OverviewOperatorAccounts operatorAccounts={model.operatorAccounts} />
        </div>
      </ResponsiveDisclosure>
    </ResponsiveDisclosureGroup>
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
  const { user } = useAuth()
  const resource = useCachedResource(resourceKey, load, { persist: false, refreshOnMount: true, refreshIntervalMs: 0, refreshOnResume: true })
  useLayoutEffect(() => () => resourceStore.cancelPending(resourceKey), [resourceKey])
  useEffect(() => { if (resource.error) onAuthorizationFailure(resource.error) }, [onAuthorizationFailure, resource.error])
  const data = resource.data?.park_id === parkId ? resource.data : undefined
  const failure = resource.error ? classifyApiError(resource.error, 'Не удалось загрузить обзор смены.') : null
  const canConfigureTracker = resource.error instanceof ApiError
    && resource.error.detail === 'tracker_token_not_configured'
    && user?.permissions?.includes('nav.admin') === true
  const canConfigurePark = resource.error instanceof ApiError
    && resource.error.detail === 'blockers_disabled_for_park'
    && user?.permissions?.includes('parks.manage') === true

  if (failure?.kind === 'unauthorized' || failure?.kind === 'forbidden') return null
  if (failure && !(data && retainable.has(failure.kind))) return <ErrorState
    action={canConfigureTracker ? <Link className="btn" to={`/admin/settings?park=${parkId}&tab=integrations#tracker-token`}>Настроить Tracker</Link>
      : canConfigurePark ? <Link className="btn" to={`/admin/settings?park=${parkId}&tab=parks`}>Настроить парк</Link> : undefined}
    description={canConfigureTracker ? 'Укажите токен Tracker в настройках интеграций, затем вернитесь к обзору.' : failure.description}
    onRetry={failure.retryable ? () => void resource.refresh() : undefined}
    requestId={failure.requestId} title={failure.title} />
  if (!data) return <LoadingState label="Загружаем обзор смены" variant="page" />

  return <>
    {failure ? <OverviewWarning busy={resource.isRevalidating} failure={failure} onRetry={() => void resource.refresh()} /> : null}
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
  const { loading, loadError, refreshParks, selectedPark, parks } = useParkScope()
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
  const retryParks = () => { void refreshParks().catch(() => undefined) }
  const firstParkSetup = user.role === 'royal' && user.permissions?.includes('parks.manage') === true && parks.length === 0

  useLayoutEffect(() => {
    if (normalized.toString() !== params.toString()) setParams(normalized, { replace: true })
  }, [normalized, params, setParams])


  return <PageLayout description={allParks ? "Приоритеты всех доступных парков. Задачи и SLA показаны отдельно по каждому парку." : "Задачи, срок SLA и простой в выбранном парке."} eyebrow="Обзор смены" title="Что требует решения сейчас">
    {contextFailure ? <ErrorState description={contextFailure.description} requestId={contextFailure.requestId} title={contextFailure.title} />
        : loading ? <LoadingState label="Загружаем область парка" variant="page" />
        : loadError && overviewParks.length === 0 ? <ErrorState description={loadError} onRetry={retryParks} title="Не удалось загрузить парки" />
        : !canReadOperations(user, 'overview') ? <ErrorState description="Для этого раздела нужны доступ к Tracker и разрешение на обзор смены." title="Нет доступа" />
          : overviewParks.length === 0 ? firstParkSetup
            ? <EmptyState action={<Link className="btn" to="/admin/settings?tab=parks">Создать первый парк</Link>} description="Создайте парк, чтобы открыть очередь задач, работу с роботами и показатели смены." icon="parks" title="Парков пока нет" />
            : <EmptyState description="Нет доступных парков для обзора смены." icon="parks" title="Парк не выбран" />
            : <>{loadError ? <div className="rp-overview-warning" role="alert"><div><strong>Не удалось обновить список парков</strong><p>{loadError}</p></div><Button leadingIcon="refresh" onClick={retryParks} variant="secondary">Повторить</Button></div> : null}{overviewParks.map(park => {
              const content = <><OverviewPark apiClient={apiClient} authorizationBlocked={authorizationBlocked} identity={identity} onAuthorizationFailure={observeAuthorizationFailure} params={params} park={park} query={query} selectable={selectable} user={user} /><CampaignOverviewSection parkId={park.id} /></>
              return allParks ? <section key={park.id} aria-label={park.name}><h2>{park.name}</h2>{content}</section> : <Fragment key={park.id}>{content}</Fragment>
            })}</>}
  </PageLayout>
}

export function OverviewPage({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  const { user } = useAuth()
  return user ? <OverviewSessionPage key={user.id} apiClient={apiClient} user={user} /> : null
}
