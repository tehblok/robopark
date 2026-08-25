import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { api, type Park, type ParkRequest } from '../api'
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { requestStatusLabel, ru } from '../i18n/ru'
import { useAuth } from '../auth-context'

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
  const [parks, setParks] = useState<Park[]>([])
  const [available, setAvailable] = useState<Park[]>([])
  const [requests, setRequests] = useState<ParkRequest[]>([])
  const [parkId, setParkId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async () => {
    const [assigned, requestable, ownRequests] = await Promise.all([
      api.operatorParks(),
      api.availableParks(),
      api.operatorParkRequests(),
    ])
    setParks(assigned)
    setAvailable(requestable)
    setRequests(ownRequests)
    setParkId((current) => current || String(requestable[0]?.id ?? ''))
  }, [])

  useEffect(() => {
    setLoading(true)
    load()
      .catch(() => setError(ru.errors.load))
      .finally(() => setLoading(false))
  }, [load])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api.requestPark(Number(parkId))
      setParkId('')
      await load()
    } catch {
      setError('Не удалось отправить заявку на парк.')
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
        {loading ? (
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
        {loading ? (
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
        {loading ? (
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
