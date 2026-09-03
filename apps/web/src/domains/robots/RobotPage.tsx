import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { canAccessRoute } from '../../app/routing/accessPolicy'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { useOnlineStatus } from '../../shared/browser/useOnlineStatus'
import { rememberRobot } from './recentRobots'
import { parseRobotReference } from './robotReference'
import { loadRelatedRobotWork, resolveRobotDetail, type RobotDetailApiClient } from './robotDetailData'
import { RobotDetailView, type RobotDetailViewProps } from './RobotDetailView'

const retainable = new Set<DomainError['kind']>(['offline', 'timeout', 'server'])
type FailureHandler = (error: unknown) => void

// The shared cache guards invalidation, but does not own an unmounted loader.
// Invalidate at layout cleanup and check ownership before every async side effect.
function useOwnedResource<T>(key: string, load: (assertCurrent: () => void) => Promise<T>, onFailure: FailureHandler) {
  const generation = useRef(0)
  useLayoutEffect(() => {
    generation.current += 1
    return () => {
      generation.current += 1
      resourceStore.invalidate(key)
    }
  }, [key])
  return useCachedResource(key, async () => {
    const requestedGeneration = generation.current
    const assertCurrent = () => {
      if (requestedGeneration !== generation.current) throw new Error('robot_detail_owner_released')
    }
    assertCurrent()
    try {
      const result = await load(assertCurrent)
      assertCurrent()
      return result
    } catch (error) {
      assertCurrent()
      onFailure(error)
      throw error
    }
  }, { persist: false })
}

function parkScope(user: User): string {
  return JSON.stringify(user.parks.filter((park) => park.is_active !== false)
    .map(({ id, tag, tracker_queue }) => [id, tag, tracker_queue])
    .sort((a, b) => Number(a[0]) - Number(b[0])))
}

function RelatedWorkOwner({ apiClient, user, resourceKey, onUnauthorized, ...view }: {
  apiClient: RobotDetailApiClient
  user: User
  resourceKey: string
  onUnauthorized: FailureHandler
} & Omit<RobotDetailViewProps, 'relatedWork' | 'relatedWorkError' | 'relatedWorkLoading' | 'onRetryWork'>) {
  const [denied, setDenied] = useState<DomainError | null>(null)
  const deniedRef = useRef<unknown>(null)
  const work = useOwnedResource(resourceKey, async () => {
    if (deniedRef.current) throw deniedRef.current
    if (!navigator.onLine) throw new TypeError('device_offline')
    return loadRelatedRobotWork(apiClient, user, view.snapshot.vin)
  }, (error) => {
    const failure = classifyApiError(error, 'Не удалось загрузить связанные задачи.')
    if (failure.kind === 'unauthorized') onUnauthorized(error)
    if (failure.kind === 'forbidden') {
      deniedRef.current = error
      resourceStore.invalidate(resourceKey)
      setDenied(failure)
    }
  })
  const allowed = (user.permissions ?? []).includes('tracker.read')
  const failure = denied ?? (work.error ? classifyApiError(work.error, 'Не удалось загрузить связанные задачи.') : null)
  return <RobotDetailView {...view} relatedWork={allowed ? work.data ?? [] : null} relatedWorkError={allowed ? failure : null} relatedWorkLoading={allowed && work.data === undefined && !failure} onRetryWork={() => void work.refresh()} />
}

function RobotResourceOwner({ apiClient, user, reference, resourceKey, onFailure }: {
  apiClient: RobotDetailApiClient
  user: User
  reference: string
  resourceKey: string
  onFailure: FailureHandler
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const search = useRef(location.search)
  useLayoutEffect(() => { search.current = location.search }, [location.search])
  const { parkId } = useParkScope()
  const browserOnline = useOnlineStatus()
  const identity = useOwnedResource(resourceKey, async (assertCurrent) => {
    if (!navigator.onLine) throw new TypeError('device_offline')
    const resolved = await resolveRobotDetail({
      ...apiClient,
      emergencyResolve: async (value) => {
        const result = await apiClient.emergencyResolve(value)
        assertCurrent()
        return result
      },
    }, reference)
    assertCurrent()
    const vin = resolved.vin.toUpperCase()
    rememberRobot(user.id, { query: reference, vin })
    if (reference !== vin) navigate(`/robots/${encodeURIComponent(vin)}${search.current}`, { replace: true })
    return { ...resolved, vin, snapshot: { ...resolved.snapshot, vin } }
  }, onFailure)
  const failure = identity.error ? classifyApiError(identity.error, 'Не удалось загрузить робота.') : null
  if (failure && (!identity.data || !retainable.has(failure.kind))) {
    return <><ErrorState {...failure} onRetry={failure.retryable ? () => void identity.refresh() : undefined} />{failure.kind === 'not-found' ? <Link to="/robots">К поиску роботов</Link> : null}</>
  }
  if (!identity.data) return <LoadingState label="Загружаем робота" variant="page" />
  const workKey = `robot-detail-work:${user.id}:${identity.data.vin}:${user.role}:${parkScope(user)}:${(user.permissions ?? []).includes('tracker.read')}`
  return <RelatedWorkOwner
    key={workKey} resourceKey={workKey} apiClient={apiClient} user={user}
    onUnauthorized={onFailure} snapshot={identity.data.snapshot} snapshotError={failure}
    browserOnline={browserOnline} parkId={parkId}
    canOpenCheck={canAccessRoute(user, 'robot-check')} canOpenWork={canAccessRoute(user, 'work')}
    workScopeLabel="доступные роли очереди Tracker; выбор парка не определяет фактический парк робота"
    onRetrySnapshot={() => void identity.refresh()}
  />
}

// This owner survives same-principal auth publications and park loading remounts.
function RobotUserPage({ apiClient, user, refreshUser }: {
  apiClient: RobotDetailApiClient
  user: User
  refreshUser: () => Promise<User>
}) {
  const { vin = '' } = useParams()
  const reference = parseRobotReference(vin)
  const { loading } = useParkScope()
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const refreshStarted = useRef(false)
  const onFailure = useCallback((error: unknown) => {
    const failure = classifyApiError(error, 'Не удалось загрузить робота.')
    if (failure.kind !== 'unauthorized' && failure.kind !== 'forbidden') return
    resourceStore.invalidate(`robot-detail-identity:${user.id}:`, { prefix: true })
    resourceStore.invalidate(`robot-detail-work:${user.id}:`, { prefix: true })
    setAuthorizationFailure(failure)
    if (failure.kind === 'unauthorized' && !refreshStarted.current) {
      refreshStarted.current = true
      void refreshUser().catch(() => undefined)
    }
  }, [refreshUser, user.id])
  const key = `robot-detail-identity:${user.id}:${reference?.toUpperCase()}:${user.role}:${parkScope(user)}:${(user.permissions ?? []).includes('nav.emergency')}`
  return <PageLayout title="Карточка робота">
    {authorizationFailure ? <ErrorState {...authorizationFailure} />
      : !reference ? <ErrorState title="Робот не указан" description="Проверьте номер или VIN робота." />
        : loading ? <LoadingState label="Загружаем область работы" variant="page" />
          : <RobotResourceOwner key={key} resourceKey={key} apiClient={apiClient} user={user} reference={reference} onFailure={onFailure} />}
  </PageLayout>
}

export function RobotPage({ apiClient = api }: { apiClient?: RobotDetailApiClient }) {
  const { user, refreshUser } = useAuth()
  if (!user) return null
  return <RobotUserPage key={user.id} apiClient={apiClient} user={user} refreshUser={refreshUser} />
}
