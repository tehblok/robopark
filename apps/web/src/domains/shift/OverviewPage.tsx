import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type Park, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { canAccessRoute } from '../../app/routing/accessPolicy'
import { Button } from '../../design-system/actions/Button'
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { resourceStore, useCachedResource } from '../../lib/resource'
import {
  classifyApiError,
  type DomainError,
} from '../../shared/api/classifyApiError'
import {
  canLoadOverviewQueue,
  loadOverview,
  type OverviewApiClient,
  type OverviewPayload,
} from './overviewData'
import { buildOverviewModel, type OverviewViewModel } from './overviewModel'
import {
  OverviewMetrics,
  OverviewPrimaryAction,
  OverviewQueue,
  OverviewRiskCard,
  OverviewScope,
  OverviewState,
} from './OverviewSections'
import './overview.css'

const retainableFailureKinds = new Set<DomainError['kind']>([
  'offline',
  'timeout',
  'server',
])

function activeParkScope(parks: Park[]): string {
  return parks
    .filter((park) => park.is_active !== false)
    .map((park) => park.id)
    .sort((a, b) => a - b)
    .join(',')
}

function overviewResourceKey(user: User, parkId: number | null, parks: Park[], selectedPark: Park | null): string {
  const scope = user.role === 'royal' ? `fleet:${activeParkScope(parks)}` : parkId
  const parkScope = (park: Park) => [park.id, park.tag?.trim(), park.tracker_queue?.trim(), park.is_active !== false]
  const access = JSON.stringify([
    user.username, user.tracker_login, user.access_status, Boolean(user.must_change_password),
    [...new Set(user.permissions ?? [])].sort(),
    [...parks].sort((a, b) => a.id - b.id).map(parkScope),
    [...user.parks].sort((a, b) => a.id - b.id).map(parkScope),
    user.role !== 'royal' && selectedPark ? parkScope(selectedPark) : null,
  ])
  return `overview:${user.id}:${user.role}:${scope}:${access}`
}

function projectOverviewPayload(
  payload: OverviewPayload | undefined,
  user: User,
  selectedPark: Park | null,
  parks: Park[],
): OverviewPayload | undefined {
  if (!payload) return undefined
  if (user.role === 'royal') {
    if (payload.kind !== 'fleet') return undefined
    const currentParks = parks.filter((park) => park.is_active !== false)
    const cachedParks = payload.summaries.map((item) => item.park)
    if (
      cachedParks.length !== currentParks.length
      || activeParkScope(cachedParks) !== activeParkScope(currentParks)
    ) return undefined
    const parksById = new Map(currentParks.map((park) => [park.id, park]))
    return {
      kind: 'fleet',
      summaries: payload.summaries.map((item) => ({
        ...item,
        park: parksById.get(item.park.id)!,
      })),
    }
  }
  if (
    payload.kind !== 'park'
    || !selectedPark
    || payload.park.id !== selectedPark.id
    || payload.summary.park_id !== selectedPark.id
  ) return undefined
  return {
    ...payload,
    park: selectedPark,
    issues: canLoadOverviewQueue(user, selectedPark) ? payload.issues : [],
  }
}

function canOpenOverviewAction(
  user: User,
  action: OverviewViewModel['primaryAction'],
): boolean {
  if (!action) return false
  const pathname = action.href.split(/[?#]/)[0]
  if (pathname === '/work' || pathname.startsWith('/work/')) {
    return canAccessRoute(user, 'work')
  }
  if (pathname === '/robots') return canAccessRoute(user, 'robots')
  if (pathname === '/admin') return canAccessRoute(user, 'admin')
  return false
}

function OverviewContent({
  model,
  user,
  showWork = true,
}: {
  model: OverviewViewModel
  user: User
  showWork?: boolean
}) {
  return (
    <div className="rp-overview">
      <OverviewScope scope={model.scope} updatedAt={model.updatedAt} freshness={model.freshness} />
      <OverviewState state={model.state} />
      <OverviewRiskCard risk={model.risk} />
      <OverviewPrimaryAction primaryAction={canOpenOverviewAction(user, model.primaryAction) ? model.primaryAction : null} />
      {showWork && canAccessRoute(user, 'work') ? <OverviewQueue queue={model.queue} /> : null}
      <OverviewMetrics metrics={model.metrics} />
    </div>
  )
}

function OverviewWarning({
  failure,
  busy,
  onRetry,
}: {
  failure: DomainError
  busy: boolean
  onRetry: () => void
}) {
  return (
    <div className="rp-overview-warning" role="alert">
      <div>
        <strong>{failure.title}</strong>
        <p>{failure.description}</p>
        {failure.requestId ? <span>Код запроса: {failure.requestId}</span> : null}
      </div>
      {failure.retryable ? (
        <Button busy={busy} leadingIcon="refresh" onClick={onRetry} variant="secondary">
          Повторить
        </Button>
      ) : null}
    </div>
  )
}

function OverviewResourceOwner({
  resourceKey,
  load,
  user,
  parkId,
  selectedPark,
  parks,
  onAuthorizationFailure,
}: {
  resourceKey: string
  load: () => Promise<OverviewPayload>
  user: User
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  onAuthorizationFailure: (error: unknown) => void
}) {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const clock = globalThis.setInterval(() => setNow(new Date()), 30_000)
    return () => globalThis.clearInterval(clock)
  }, [])

  const overview = useCachedResource(resourceKey, load)
  // Error ownership belongs to the currently rendered resource, even when
  // StrictMode or a same-access remount coalesces an earlier request.
  useEffect(() => {
    if (overview.error) onAuthorizationFailure(overview.error)
  }, [onAuthorizationFailure, overview.error])
  const payload = projectOverviewPayload(overview.data, user, selectedPark, parks)
  const failure = overview.error
    ? classifyApiError(overview.error, 'Не удалось загрузить обзор смены.')
    : null
  const canRetainData = Boolean(
    payload && failure && retainableFailureKinds.has(failure.kind),
  )

  // The principal boundary owns the denial UI; never briefly paint a second
  // error node (or cached data) while that boundary is being notified.
  if (failure?.kind === 'unauthorized' || failure?.kind === 'forbidden') return null
  if (failure && !canRetainData) {
    return (
      <ErrorState
        description={failure.description}
        onRetry={failure.retryable ? () => void overview.refresh() : undefined}
        requestId={failure.requestId}
        title={failure.title}
      />
    )
  }
  if (!payload) {
    return <LoadingState label="Загружаем обзор смены" variant="page" />
  }

  const model = buildOverviewModel({
    role: user.role,
    payload,
    parkId,
    canOpenAdministration: canAccessRoute(user, 'admin'),
  }, now)
  const visibleModel: OverviewViewModel = failure && model.updatedAt
    ? { ...model, freshness: failure.kind === 'offline' ? 'offline' : 'stale' }
    : model

  return (
    <>
      {failure ? (
        <OverviewWarning
          busy={overview.isRevalidating}
          failure={failure}
          onRetry={() => void overview.refresh()}
        />
      ) : null}
      <OverviewContent model={visibleModel} user={user} />
    </>
  )
}

function OverviewUserPage({
  apiClient,
  user,
  refreshUser,
  parkId,
  selectedPark,
  parks,
  loading,
}: {
  apiClient: OverviewApiClient
  user: User
  refreshUser: () => Promise<User>
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
}) {
  const cachePrefix = `overview:${user.id}:`
  const blockedRef = useRef(false)
  const blockedErrorRef = useRef<unknown>(null)
  const refreshStartedRef = useRef(false)
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const resourceKey = overviewResourceKey(user, parkId, parks, selectedPark)
  const currentAccess = useRef({ key: resourceKey, generation: 0 })
  const committedResourceKey = useRef(resourceKey)
  useLayoutEffect(() => {
    if (currentAccess.current.key !== resourceKey) {
      currentAccess.current = { key: resourceKey, generation: currentAccess.current.generation + 1 }
    }
    if (committedResourceKey.current !== resourceKey) {
      resourceStore.invalidate(committedResourceKey.current)
      committedResourceKey.current = resourceKey
    }
  }, [resourceKey])

  const observeAuthorizationFailure = useCallback((error: unknown) => {
    const failure = classifyApiError(error, 'Не удалось загрузить обзор смены.')
    if (failure.kind !== 'unauthorized' && failure.kind !== 'forbidden') return
    blockedRef.current = true
    blockedErrorRef.current = error
    resourceStore.invalidate(cachePrefix, { prefix: true })
    setAuthorizationFailure(failure)
    if (!refreshStartedRef.current) {
      refreshStartedRef.current = true
      void refreshUser().catch(() => undefined)
    }
  }, [cachePrefix, refreshUser])

  const guardedLoad = useCallback(async () => {
    const accessGeneration = currentAccess.current.generation
    const assertCurrentAccess = () => {
      if (currentAccess.current.key !== resourceKey || currentAccess.current.generation !== accessGeneration) {
        throw new Error('overview_access_changed')
      }
      if (blockedRef.current) {
        throw blockedErrorRef.current ?? new Error('overview_authorization_blocked')
      }
    }
    assertCurrentAccess()
    const next = await loadOverview({
      ...apiClient,
      dashboardSummary: async (id) => {
        const summary = await apiClient.dashboardSummary(id)
        // Do not enter the old queue stage after access was replaced while
        // the summary request was pending.
        assertCurrentAccess()
        return summary
      },
    }, user, selectedPark, parks)
    assertCurrentAccess()
    return next
  }, [apiClient, parks, resourceKey, selectedPark, user])

  if (authorizationFailure) {
    return (
      <PageLayout title="Смена / Обзор">
        <ErrorState
          description={authorizationFailure.description}
          requestId={authorizationFailure.requestId}
          title={authorizationFailure.title}
        />
      </PageLayout>
    )
  }
  if (loading) {
    return <LoadingState label="Загружаем область работы" variant="page" />
  }
  if (user.role !== 'driver' && user.role !== 'royal' && !selectedPark) {
    return (
      <EmptyState
        description="Выберите доступный парк, чтобы увидеть состояние смены."
        icon="parks"
        title="Парк не выбран"
      />
    )
  }

  return (
    <PageLayout title="Смена / Обзор">
      {user.role === 'driver' ? (
        <OverviewContent
          model={buildOverviewModel({
            role: user.role,
            payload: { kind: 'driver' },
            parkId,
            canOpenAdministration: canAccessRoute(user, 'admin'),
          }, new Date())}
          showWork={false}
          user={user}
        />
      ) : (
        <OverviewResourceOwner
          key={resourceKey}
          load={guardedLoad}
          onAuthorizationFailure={observeAuthorizationFailure}
          parkId={parkId}
          parks={parks}
          resourceKey={resourceKey}
          selectedPark={selectedPark}
          user={user}
        />
      )}
    </PageLayout>
  )
}

export function OverviewPage({ apiClient = api }: { apiClient?: OverviewApiClient }) {
  const { user, refreshUser } = useAuth()
  const { parkId, selectedPark, parks, loading } = useParkScope()

  if (!user) return null

  return (
    <OverviewUserPage
      apiClient={apiClient}
      key={user.id}
      loading={loading}
      parkId={parkId}
      parks={parks}
      refreshUser={refreshUser}
      selectedPark={selectedPark}
      user={user}
    />
  )
}
