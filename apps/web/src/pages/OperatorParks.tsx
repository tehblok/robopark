import { useEffect, useState } from 'react'
import { api, type Park, type ParkRequest } from '../api'
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { RequestParkModal } from '../components/parks/RequestParkModal'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { requestStatusLabel, ru } from '../i18n/ru'
import { useAuth } from '../auth-context'
import { useCachedResource } from '../lib/resource'

function requestBadgeClass(status: string): string {
  switch (status) {
    case 'approved':
      return 'badge badge-ok'
    case 'rejected':
      return 'badge badge-danger'
    case 'pending':
      return 'badge badge-warn'
    default:
      return 'badge badge-muted'
  }
}

export function OperatorParks() {
  const { refreshUser } = useAuth()
  const [parkModalOpen, setParkModalOpen] = useState(false)

  useEffect(() => {
    void refreshUser()
    // AuthProvider recreates refreshUser each render; mount-only refresh is required.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only
  }, [])

  const parksRes = useCachedResource<Park[]>('operator:parks', () => api.operatorParks())
  const availableRes = useCachedResource<Park[]>('operator:available-parks', () => api.availableParks())
  const requestsRes = useCachedResource<ParkRequest[]>(
    'operator:park-requests',
    () => api.operatorParkRequests(),
  )

  const parks = parksRes.data ?? []
  const available = availableRes.data ?? []
  const requests = requestsRes.data ?? []

  const parksLoading = parksRes.isLoading && !parksRes.data
  const requestsLoading = requestsRes.isLoading && !requestsRes.data

  const loadError = parksRes.error ?? availableRes.error ?? requestsRes.error
  const error = loadError ? mapApiError(loadError, ru.errors.load) : ''

  const refreshAll = () =>
    Promise.all([parksRes.refresh(), availableRes.refresh(), requestsRes.refresh()])

  return (
    <>
      <PageShell
        actions={
          <button className="btn btn-secondary" onClick={() => setParkModalOpen(true)} type="button">
            {ru.parks.requestPark}
          </button>
        }
        subtitle="Ваши парки, заявки на доступ и история запросов."
        title={ru.parks.myParks}
      >
        {error && <Alert tone="error">{error}</Alert>}

        <Panel hint="Парки, к которым администратор уже выдал доступ." title={ru.parks.myParks}>
          {parksLoading ? (
            <SkeletonList rows={2} />
          ) : parks.length ? (
            <ul className="park-card-list">
              {parks.map((park) => (
                <li className="park-card" key={park.id}>
                  <div className="park-card-title">{park.name}</div>
                  <div className="park-card-meta">
                    <span className="badge badge-muted">{park.tag}</span>
                    <Badge active={park.is_active ?? true} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyBlock
              action={
                <button className="btn" onClick={() => setParkModalOpen(true)} type="button">
                  {ru.parks.requestPark}
                </button>
              }
              hint={ru.parks.noAssignedParksHint}
              icon="🏭"
              title={ru.parks.noAssignedParks}
            />
          )}
        </Panel>

        <Panel
          hint={
            available.length
              ? ru.parks.requestParkHint
              : ru.parks.requestParkEmptyHint
          }
          title={ru.parks.requestPark}
        >
          <div className="form-actions">
            <button
              className="btn"
              disabled={!available.length && !availableRes.isLoading}
              onClick={() => setParkModalOpen(true)}
              type="button"
            >
              {ru.parks.requestPark}
            </button>
          </div>
          {!available.length && !availableRes.isLoading && (
            <EmptyBlock icon="✓" title={ru.parks.requestParkEmpty} />
          )}
        </Panel>

        <Panel title="Мои заявки">
          {requestsLoading ? (
            <SkeletonList rows={2} />
          ) : requests.length ? (
            <ul className="park-card-list">
              {requests.map((request) => (
                <li className="park-card" key={request.id}>
                  <div className="park-card-title">Парк #{request.park_id}</div>
                  <div className="park-card-meta">
                    <span className={requestBadgeClass(request.status)}>
                      {requestStatusLabel(request.status)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyBlock icon="📥" title={ru.empty} />
          )}
        </Panel>
      </PageShell>
      <RequestParkModal
        onClose={() => setParkModalOpen(false)}
        onSubmitted={() => {
          void refreshUser()
          void refreshAll()
        }}
        open={parkModalOpen}
      />
    </>
  )
}
