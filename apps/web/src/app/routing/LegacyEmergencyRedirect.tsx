import { useLayoutEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth-context'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { checkAccessIdentity } from '../../domains/robots/robotCheckUrl'
import { ru } from '../../i18n/ru'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'

export type LegacyRobotCheckApiClient = Pick<typeof api, 'emergencyResolve'>

function ResolveLegacyRobot({ reference, search, apiClient }: {
  reference: string; search: string; apiClient: LegacyRobotCheckApiClient
}) {
  const [vin, setVin] = useState<string | null>(null)
  const [failure, setFailure] = useState<DomainError | null>(null)
  const [retry, setRetry] = useState(0)
  const generation = useRef(0)
  useLayoutEffect(() => {
    const requestedGeneration = ++generation.current
    const current = () => requestedGeneration === generation.current
    setFailure(null)
    void Promise.resolve().then(() => {
      if (current()) return apiClient.emergencyResolve(reference)
    }).then(result => {
      if (current() && result) setVin(result.vin.trim().toUpperCase())
    }, error => {
      if (current()) setFailure(classifyApiError(error, ru.errors.emergency))
    })
    return () => { generation.current += 1 }
  }, [apiClient, reference, retry])

  if (vin) return <Navigate replace to={`/robots/${encodeURIComponent(vin)}/check${search}`} />
  if (!failure) return <LoadingState label="Находим робота" variant="page" />
  const park = new URLSearchParams(search).get('park')
  return <>
    <ErrorState title={failure.title} description={failure.description} requestId={failure.requestId}
      onRetry={failure.retryable ? () => setRetry(value => value + 1) : undefined} />
    <Link to={`/robots${park ? `?park=${park}` : ''}`}>К поиску роботов</Link>
  </>
}

export function LegacyEmergencyRedirect({ apiClient = api }: { apiClient?: LegacyRobotCheckApiClient }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const reference = (params.get('q') ?? params.get('robot') ?? '').trim()
  const search = new URLSearchParams()
  const park = params.get('park')
  if (park && /^\d+$/.test(park)) search.set('park', park)
  const robotsPath = `/robots${search.size ? `?${search}` : ''}`
  const tab = params.get('tab')?.trim()
  if (tab) search.set('tab', tab)
  if (!user || loading) return null
  if (!reference) return <Navigate replace to={robotsPath} />

  // Remount ownership on principal, authorization or input changes. Layout
  // cleanup invalidates the old generation before any late completion can navigate.
  const identity = JSON.stringify([user.id, checkAccessIdentity(user), location.search])
  return <ResolveLegacyRobot key={identity} reference={reference} search={search.size ? `?${search}` : ''} apiClient={apiClient} />
}
