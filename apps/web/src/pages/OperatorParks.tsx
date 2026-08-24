import { type FormEvent, useEffect, useState } from 'react'
import { api, type Park, type ParkRequest } from '../api'
import { Alert, Badge, EmptyState, PageShell, Panel } from '../components/PageShell'
import { requestStatusLabel, ru } from '../i18n/ru'
import { useAuth } from '../auth-context'

export function OperatorParks() {
  const { logout } = useAuth()
  const [parks, setParks] = useState<Park[]>([])
  const [available, setAvailable] = useState<Park[]>([])
  const [requests, setRequests] = useState<ParkRequest[]>([])
  const [parkId, setParkId] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    const [assigned, requestable, ownRequests] = await Promise.all([
      api.operatorParks(),
      api.availableParks(),
      api.operatorParkRequests(),
    ])
    setParks(assigned)
    setAvailable(requestable)
    setRequests(ownRequests)
    setParkId((current) => current || String(requestable[0]?.id ?? ''))
  }

  useEffect(() => {
    load().catch(() => setError(ru.errors.load))
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      await api.requestPark(Number(parkId))
      setParkId('')
      await load()
    } catch {
      setError('Не удалось отправить заявку на парк.')
    }
  }

  return (
    <PageShell
      backTo="/operator"
      onLogout={logout}
      subtitle="Ваши парки, заявки на доступ и история запросов."
      title="Мои парки"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Парки, к которым администратор уже выдал доступ." title="Мои парки">
        {parks.length ? (
          <ul className="card-list">
            {parks.map((park) => (
              <li className="card" key={park.id}>
                <div className="card-title">{park.name}</div>
                <div className="card-meta">
                  <span>Тег: {park.tag}</span>
                  <Badge active={park.is_active ?? true} />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>Пока нет назначенных парков. Отправьте заявку ниже или дождитесь одобрения регистрации.</EmptyState>
        )}
      </Panel>

      <Panel hint="Можно запросить только активные парки, к которым у вас ещё нет доступа." title="Запросить парк">
        <form className="inline-form" onSubmit={submit}>
          <select
            aria-label="Парк"
            disabled={!available.length}
            onChange={(event) => setParkId(event.target.value)}
            value={parkId}
          >
            {available.map((park) => (
              <option key={park.id} value={park.id}>{park.name} ({park.tag})</option>
            ))}
          </select>
          <button disabled={!parkId} type="submit">Отправить заявку</button>
        </form>
        {!available.length && (
          <EmptyState>Нет доступных парков для запроса — возможно, вы уже привязаны ко всем активным паркам.</EmptyState>
        )}
      </Panel>

      <Panel title="Мои заявки">
        {requests.length ? (
          <ul className="card-list">
            {requests.map((request) => (
              <li className="card" key={request.id}>
                <div className="card-title">Парк #{request.park_id}</div>
                <div className="card-meta">
                  <span>Статус: {requestStatusLabel(request.status)}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>{ru.empty}</EmptyState>
        )}
      </Panel>
    </PageShell>
  )
}
