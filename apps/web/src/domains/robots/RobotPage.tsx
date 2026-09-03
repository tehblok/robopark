import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { canAccessRoute } from '../../app/routing/accessPolicy'
import { Button } from '../../design-system/actions/Button'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { useOnlineStatus } from '../../shared/browser/useOnlineStatus'
import { CheckError, RobotCheckWorkspace, type RobotCheckApiClient } from './RobotCheckWorkspace'
import type { RobotResolverApiClient } from './RobotResolver'
import { buildRobotCheckSearch, checkAccessIdentity, classifyCheckError, parseRobotCheckTab } from './robotCheckUrl'
import { rememberRobot } from './recentRobots'
import { parseRobotReference } from './robotReference'
import { loadRelatedRobotWork, type RobotDetailApiClient } from './robotDetailData'
import { RobotDetailView, type RobotDetailViewProps } from './RobotDetailView'

type FailureHandler = (failure: DomainError) => void
type Resolved = Awaited<ReturnType<RobotResolverApiClient['emergencyResolve']>>
type Clients = { apiClient: RobotDetailApiClient; resolverClient: RobotResolverApiClient; checkClient: RobotCheckApiClient }

function RelatedWorkOwner({ apiClient, user, reference, resourceKey, onAuthorizationFailure, ...view }: {
  apiClient: RobotDetailApiClient; user: User; reference: string; resourceKey: string
  onAuthorizationFailure: FailureHandler
} & Omit<RobotDetailViewProps, 'relatedWork' | 'relatedWorkError' | 'relatedWorkLoading' | 'onRetryWork' | 'reference'>) {
  const generation = useRef(0)
  const [denied, setDenied] = useState<DomainError | null>(null)
  const deniedRef = useRef<unknown>(null)
  useLayoutEffect(() => {
    generation.current += 1
    return () => { generation.current += 1; resourceStore.invalidate(resourceKey) }
  }, [resourceKey])
  const allowed = (user.permissions ?? []).includes('tracker.read')
  const work = useCachedResource(resourceKey, async () => {
    const requested = generation.current
    const current = () => requested === generation.current
    try {
      if (deniedRef.current) throw deniedRef.current
      if (!navigator.onLine) throw new TypeError('device_offline')
      const value = await loadRelatedRobotWork(apiClient, user, reference)
      if (!current()) throw new Error('robot_work_owner_released')
      return value
    } catch (error) {
      if (!current()) throw error
      const failure = classifyApiError(error, 'Не удалось загрузить связанные задачи.')
      if (failure.kind === 'unauthorized') onAuthorizationFailure(failure)
      if (failure.kind === 'forbidden') {
        deniedRef.current = error
        resourceStore.invalidate(resourceKey)
        setDenied(failure)
      }
      throw error
    }
  }, { persist: false, enabled: allowed })
  const failure = denied ?? (work.error ? classifyApiError(work.error, 'Не удалось загрузить связанные задачи.') : null)
  return <RobotDetailView {...view} reference={reference} relatedWork={allowed ? work.data ?? [] : null}
    relatedWorkError={allowed ? failure : null} relatedWorkLoading={allowed && work.data === undefined && !failure}
    onRetryWork={() => void work.refresh()} />
}

function RobotResourceOwner({ apiClient, resolverClient, checkClient, user, reference, onAuthorizationFailure, onResolved }: Clients & {
  user: User; reference: string; onAuthorizationFailure: FailureHandler; onResolved: (value: Resolved) => void
}) {
  const [resolved, setResolved] = useState<Resolved | null>(null)
  const [failure, setFailure] = useState<DomainError | null>(null)
  const [retry, setRetry] = useState(0)
  const manual = useRef(false)
  const generation = useRef(0)
  const online = useOnlineStatus()
  const { parkId } = useParkScope()
  const canCheck = canAccessRoute(user, 'robot-check')
  const notifyResolved = useRef(onResolved)
  useLayoutEffect(() => { notifyResolved.current = onResolved }, [onResolved])
  useLayoutEffect(() => {
    const requested = ++generation.current
    const current = () => requested === generation.current
    const release = () => { generation.current += 1 }
    const requestedManually = manual.current
    manual.current = false
    if (!canCheck || (!online && !requestedManually)) return release
    setFailure(null)
    void Promise.resolve().then(() => current() ? resolverClient.emergencyResolve(reference) : undefined).then(value => {
      if (!current() || !value) return
      const normalized = { ...value, vin: value.vin.toUpperCase() }
      setResolved(normalized)
      rememberRobot(user.id, { query: reference, vin: normalized.vin })
      notifyResolved.current(normalized)
    }, error => {
      if (!current()) return
      const classified = classifyCheckError(error)
      if (classified.kind === 'unauthorized' || classified.kind === 'forbidden') onAuthorizationFailure(classified)
      else setFailure(classified)
    })
    return release
  }, [reference, resolverClient, user.id, canCheck, retry, online, onAuthorizationFailure])

  const navigate = useNavigate()
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const activeTab = parseRobotCheckTab(params, resolved?.sections ?? [])
  const suffix = location.pathname.endsWith('/check') ? '/check' : ''
  // Alias-aware ownership avoids a second resolver and snapshot on URL replacement.
  const canonical = resolved ? `/robots/${encodeURIComponent(resolved.vin)}${suffix}${buildRobotCheckSearch(params, activeTab)}` : null
  useLayoutEffect(() => {
    if (canonical && `${location.pathname}${location.search}` !== canonical) navigate(canonical, { replace: true })
  }, [canonical, location.pathname, location.search, navigate])

  const refresh = () => { manual.current = true; setRetry(value => value + 1) }
  const robotReference = resolved?.vin ?? reference
  const workKey = `robot-detail-work:${user.id}:${robotReference}:${checkAccessIdentity(user)}:${parkId}`
  const detail = (snapshot: RobotDetailViewProps['snapshot'], snapshotError: DomainError | null, onRetrySnapshot: () => void) => <RelatedWorkOwner
    key={workKey} resourceKey={workKey} apiClient={apiClient} user={user} reference={robotReference}
    onAuthorizationFailure={onAuthorizationFailure} snapshot={snapshot} snapshotError={snapshotError}
    browserOnline={online} parkId={parkId} canOpenCheck={false} canOpenWork={canAccessRoute(user, 'work')}
    workScopeLabel="доступные роли очереди Tracker; выбор парка не определяет фактический парк робота"
    onRetrySnapshot={onRetrySnapshot} />
  if (!canCheck) return detail(null, null, () => undefined)
  if (failure) return <><CheckError failure={failure} user={user} onRetry={refresh} />{failure.kind === 'not-found' ? <Link to="/robots">К поиску роботов</Link> : null}</>
  if (!resolved) return !online ? <><p role="status">Нет сети на этом устройстве</p><Button onClick={refresh}>Повторить проверку</Button></> : <LoadingState label="Находим робота" variant="page" />
  return <RobotCheckWorkspace vin={resolved.vin} sections={resolved.sections} activeTab={activeTab} user={user}
    apiClient={checkClient} onAuthorizationFailure={onAuthorizationFailure} renderSummary={detail}
    onTabChange={tab => navigate(`/robots/${encodeURIComponent(resolved.vin)}${suffix}${buildRobotCheckSearch(new URLSearchParams(location.search), tab)}`, { replace: true })} />
}

// The principal boundary owns durable once-only 401 refresh and scope-local 403
// denial above park-loading/resource remounts, for both compatible entry routes.
function RobotUserPage({ apiClient, resolverClient, checkClient, user, refreshUser }: Clients & { user: User; refreshUser: () => Promise<User> }) {
  const { vin = '' } = useParams()
  const reference = parseRobotReference(vin)
  const { loading, parkId } = useParkScope()
  const location = useLocation()
  const requestedPark = new URLSearchParams(location.search).get('park')
  // The provider temporarily clears selection while loading; the same requested
  // park must not reset a denial. This identity never grants API access.
  const scope = `${checkAccessIdentity(user)}:${requestedPark && /^\d+$/.test(requestedPark) ? requestedPark : parkId}`
  const [alias, setAlias] = useState<{ scope: string; reference: string; vin: string } | null>(null)
  const ownerReference = alias?.scope === scope && reference?.toUpperCase() === alias.vin ? alias.reference : reference
  const identity = `${scope}:${ownerReference}`
  const [unauthorized, setUnauthorized] = useState<DomainError | null>(null)
  const [forbidden, setForbidden] = useState<{ identity: string; failure: DomainError } | null>(null)
  const refreshStarted = useRef(false)
  const owner = useRef<string | null>(identity)
  useLayoutEffect(() => { owner.current = identity; return () => { owner.current = null } }, [identity])
  const refresh = useRef(refreshUser)
  useLayoutEffect(() => { refresh.current = refreshUser }, [refreshUser])
  const onAuthorizationFailure = useCallback((failure: DomainError) => {
    if (owner.current !== identity) return
    resourceStore.invalidate(`robot-detail-work:${user.id}:`, { prefix: true })
    if (failure.kind === 'unauthorized') {
      setUnauthorized(failure)
      if (!refreshStarted.current) { refreshStarted.current = true; void refresh.current().catch(() => undefined) }
    } else if (failure.kind === 'forbidden') setForbidden({ identity, failure })
  }, [identity, user.id])
  const denial = unauthorized ?? (forbidden?.identity === identity ? forbidden.failure : null)
  return <PageLayout title="Рабочее пространство робота">
    <nav className="rp-check-backlinks" aria-label="Навигация робота"><Link to="/robots">Все роботы</Link></nav>
    {denial ? <CheckError failure={denial} user={user} />
      : !reference ? <ErrorState title="Робот не указан" description="Проверьте номер или VIN робота." />
        : loading ? <LoadingState label="Загружаем область работы" variant="page" />
          : <RobotResourceOwner key={identity} apiClient={apiClient} resolverClient={resolverClient} checkClient={checkClient}
            user={user} reference={ownerReference!} onAuthorizationFailure={onAuthorizationFailure}
            onResolved={value => setAlias({ scope, reference: ownerReference!, vin: value.vin })} />}
  </PageLayout>
}

export function RobotPage({ apiClient = api, resolverClient = apiClient, checkClient }: {
  apiClient?: RobotDetailApiClient; resolverClient?: RobotResolverApiClient; checkClient?: RobotCheckApiClient
}) {
  const { user, refreshUser } = useAuth()
  const diagnostics = useMemo(() => checkClient ?? { emergencySnapshot: apiClient.emergencySnapshot, emergencySection: api.emergencySection }, [apiClient, checkClient])
  return user ? <RobotUserPage key={user.id} apiClient={apiClient} resolverClient={resolverClient} checkClient={diagnostics} user={user} refreshUser={refreshUser} /> : null
}
