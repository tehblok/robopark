import { type FormEvent, useEffect, useState } from 'react'
import { api, type Park, type ParkRequest } from '../api'
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
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
  const { logout } = useAuth()
  const [parkId, setParkId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

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
  const availableLoading = availableRes.isLoading && !availableRes.data
  const requestsLoading = requestsRes.isLoading && !requestsRes.data

  const loadError = parksRes.error ?? availableRes.error ?? requestsRes.error
  const error =
    submitError || (loadError ? mapApiError(loadError, ru.errors.load) : '')

  useEffect(() => {
    if (!parkId && available.length) {
      setParkId(String(available[0].id))
    }
  }, [parkId, available])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitError('')
    setSubmitting(true)
    try {
      await api.requestPark(Number(parkId))
      setParkId('')
      await Promise.all([parksRes.refresh(), availableRes.refresh(), requestsRes.refresh()])
    } catch {
      setSubmitError('Не удалось отправить заявку на парк.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Ваши парки, заявки на доступ и история запросов."
      title="Мои парки"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Парки, к которым администратор уже выдал доступ." title="Мои парки">
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
            hint="Отправьте заявку ниже или дождитесь одобрения регистрации."
            icon="🏭"
            title="Пока нет назначенных парков"
          />
        )}
      </Panel>

      <Panel hint="Можно запросить только активные парки, к которым у вас ещё нет доступа." title="Запросить парк">
        {availableLoading ? (
          <SkeletonList rows={1} />
        ) : (
          <>
            <form className="form-grid" onSubmit={(event) => void submit(event)}>
              <label className="field">
                <span className="field-label">Парк</span>
                <select
                  aria-label="Парк"
                  disabled={!available.length || submitting}
                  onChange={(event) => setParkId(event.target.value)}
                  value={parkId}
                >
                  {available.map((park) => (
                    <option key={park.id} value={park.id}>
                      {park.name} ({park.tag})
                    </option>
                  ))}
                </select>
              </label>
              <div className="form-actions">
                <button className="btn" disabled={!parkId || submitting} type="submit">
                  {submitting ? <Spinner label="Отправка" /> : 'Отправить заявку'}
                </button>
              </div>
            </form>
            {!available.length && (
              <EmptyBlock
                hint="Возможно, вы уже привязаны ко всем активным паркам."
                icon="✓"
                title="Нет парков для запроса"
              />
            )}
          </>
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
  )
}
