import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import type { DomainError } from '../../shared/api/classifyApiError'
import { useOnlineStatus } from '../../shared/browser/useOnlineStatus'
import { CheckError, RobotCheckWorkspace, type RobotCheckApiClient } from './RobotCheckWorkspace'
import type { RobotResolverApiClient } from './RobotResolver'
import { buildRobotCheckSearch, checkAccessIdentity, classifyCheckError, parseRobotCheckTab } from './robotCheckUrl'
import { parseRobotReference } from './robotReference'
import { rememberRobot } from './recentRobots'

type Clients = { resolverClient?: RobotResolverApiClient; checkClient?: RobotCheckApiClient }
type Resolved = Awaited<ReturnType<RobotResolverApiClient['emergencyResolve']>>

function ResolvedCheckOwner({ reference, user, resolverClient, checkClient, onAuthorizationFailure }: Required<Clients> & {
  reference: string; user: User; onAuthorizationFailure: (failure: DomainError) => void
}) {
  const [resolved, setResolved] = useState<Resolved | null>(null)
  const [failure, setFailure] = useState<DomainError | null>(null)
  const [retry, setRetry] = useState(0)
  const manual = useRef(false)
  const generation = useRef(0)
  const online = useOnlineStatus()
  const navigate = useNavigate()
  const location = useLocation()
  const search = useRef(location.search)
  useLayoutEffect(() => { search.current = location.search }, [location.search])
  useLayoutEffect(() => {
    const requestedGeneration = ++generation.current
    const current = () => requestedGeneration === generation.current
    const release = () => { generation.current += 1 }
    const requestedManually = manual.current
    manual.current = false
    if (!online && !requestedManually) return release
    setFailure(null)
    void Promise.resolve().then(() => {
      if (current()) return resolverClient.emergencyResolve(reference)
    }).then(value => {
      if (!current() || !value) return
      const normalized = { ...value, vin: value.vin.toUpperCase() }
      rememberRobot(user.id, { query: reference, vin: normalized.vin })
      setResolved(normalized)
    }, error => {
      if (!current()) return
      const classified = classifyCheckError(error)
      if (classified.kind === 'unauthorized' || classified.kind === 'forbidden') onAuthorizationFailure(classified)
      else setFailure(classified)
    })
    return release
  }, [reference, user.id, resolverClient, retry, online, onAuthorizationFailure])
  const params = new URLSearchParams(location.search)
  const activeTab = resolved ? parseRobotCheckTab(params, resolved.sections) : 'map'
  const canonical = resolved ? `/robots/${encodeURIComponent(resolved.vin)}/check${buildRobotCheckSearch(params, activeTab)}` : null
  useLayoutEffect(() => {
    if (canonical && `${location.pathname}${location.search}` !== canonical) navigate(canonical, { replace: true })
  }, [canonical, location.pathname, location.search, navigate])
  const refresh = () => { manual.current = true; setRetry(value => value + 1) }
  if (failure) return <><CheckError failure={failure} user={user} onRetry={refresh} />{failure.kind === 'not-found' ? <Link to="/robots">К поиску роботов</Link> : null}</>
  if (!resolved) return !online ? <><p role="status">Нет сети на этом устройстве</p><Button onClick={refresh}>Повторить проверку</Button></> : <LoadingState label="Находим робота" variant="page" />
  const parkSearch = buildRobotCheckSearch(params, 'map')
  return <>
    <nav className="rp-check-backlinks" aria-label="Навигация робота"><Link to={`/robots/${encodeURIComponent(resolved.vin)}${parkSearch}`}>Карточка робота</Link><Link to="/robots">Все роботы</Link></nav>
    <RobotCheckWorkspace vin={resolved.vin} sections={resolved.sections} activeTab={activeTab} user={user} apiClient={checkClient} onAuthorizationFailure={onAuthorizationFailure}
      onTabChange={tab => navigate(`/robots/${encodeURIComponent(resolved.vin)}/check${buildRobotCheckSearch(new URLSearchParams(search.current), tab)}`, { replace: true })} />
  </>
}

// Principal owner is above resolver/workspace/loading remounts. A same-ID auth
// publication must never reset its once-only refresh boundary.
function PrincipalCheckPage({ user, refreshUser, resolverClient, checkClient }: Required<Clients> & { user: User; refreshUser: () => Promise<User> }) {
  const { vin = '' } = useParams()
  const reference = parseRobotReference(vin)
  const { loading } = useParkScope()
  const identity = `${reference?.toUpperCase()}:${checkAccessIdentity(user)}`
  const [unauthorized, setUnauthorized] = useState<DomainError | null>(null)
  const [forbidden, setForbidden] = useState<{ identity: string; failure: DomainError } | null>(null)
  const refreshStarted = useRef(false)
  const owner = useRef<string | null>(identity)
  useLayoutEffect(() => { owner.current = identity; return () => { owner.current = null } }, [identity])
  const refresh = useRef(refreshUser)
  useLayoutEffect(() => { refresh.current = refreshUser }, [refreshUser])
  const onAuthorizationFailure = useCallback((failure: DomainError) => {
    if (owner.current !== identity) return
    if (failure.kind === 'unauthorized') {
      setUnauthorized(failure)
      if (!refreshStarted.current) { refreshStarted.current = true; void refresh.current().catch(() => undefined) }
    } else if (failure.kind === 'forbidden') setForbidden({ identity, failure })
  }, [identity])
  const denial = unauthorized ?? (forbidden?.identity === identity ? forbidden.failure : null)
  return <PageLayout title="Проверка робота">
    {denial ? <CheckError failure={denial} user={user} />
      : !reference ? <ErrorState title="Робот не указан" description="Проверьте номер или VIN робота." />
        : loading ? <LoadingState label="Загружаем область работы" variant="page" />
          : <ResolvedCheckOwner key={identity} reference={reference} user={user} resolverClient={resolverClient} checkClient={checkClient} onAuthorizationFailure={onAuthorizationFailure} />}
  </PageLayout>
}
export function RobotCheckPage({ resolverClient = api, checkClient = api }: Clients) {
  const { user, refreshUser } = useAuth()
  return user ? <PrincipalCheckPage key={user.id} user={user} refreshUser={refreshUser} resolverClient={resolverClient} checkClient={checkClient} /> : null
}
