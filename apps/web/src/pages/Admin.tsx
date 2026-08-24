import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type AccessRequest,
  type IntegrationSettings,
  type Mechanic,
  type Park,
  type ParkRequest,
  type TrackerPolicySettings,
} from '../api'
import { Alert, Badge, EmptyState, PageShell, Panel } from '../components/PageShell'
import { ru } from '../i18n/ru'

function cookieBadge(valid: boolean | null | undefined) {
  if (valid === true) return <span className="badge badge-ok">cookie действует</span>
  if (valid === false) return <span className="badge badge-danger">cookie недействителен</span>
  return <span className="badge badge-warn">не проверялся</span>
}

export function Admin() {
  const [parks, setParks] = useState<Park[]>([])
  const [accessRequests, setAccessRequests] = useState<AccessRequest[]>([])
  const [parkRequests, setParkRequests] = useState<ParkRequest[]>([])
  const [mechanics, setMechanics] = useState<Mechanic[]>([])
  const [settings, setSettings] = useState<IntegrationSettings | null>(null)
  const [trackerPolicy, setTrackerPolicy] = useState<TrackerPolicySettings | null>(null)
  const [selections, setSelections] = useState<Record<number, number[]>>({})
  const [name, setName] = useState('')
  const [tag, setTag] = useState('')
  const [trackerToken, setTrackerToken] = useState('')
  const [emergencyCookie, setEmergencyCookie] = useState('')
  const [mechanicUsername, setMechanicUsername] = useState('')
  const [mechanicPassword, setMechanicPassword] = useState('')
  const [mechanicParkId, setMechanicParkId] = useState('')
  const [mechanicDrafts, setMechanicDrafts] = useState<
    Record<number, { parkId: string; password: string }>
  >({})
  const [error, setError] = useState('')

  const load = async () => {
    const [parkList, accessInbox, parkInbox, mechanicList, integration, policy] =
      await Promise.all([
        api.parks(),
        api.accessRequests(),
        api.adminParkRequests(),
        api.mechanics(),
        api.integrationSettings(),
        api.trackerPolicy(),
      ])
    setParks(parkList)
    setAccessRequests(accessInbox)
    setParkRequests(parkInbox)
    setMechanics(mechanicList)
    setSettings(integration)
    setTrackerPolicy(policy)
    setMechanicDrafts(Object.fromEntries(
      mechanicList.map((mechanic) => [
        mechanic.id,
        { parkId: String(mechanic.park.id), password: '' },
      ]),
    ))
    setMechanicParkId((current) => current || String(parkList.find((park) => park.is_active)?.id ?? ''))
  }

  useEffect(() => {
    load().catch(() => setError(ru.errors.load))
  }, [])

  const run = async (action: () => Promise<unknown>) => {
    setError('')
    try {
      await action()
      await load()
    } catch {
      setError(ru.errors.generic)
    }
  }

  const createPark = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createPark({ name, tag })
      setName('')
      setTag('')
    })
  }

  const createMechanic = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createMechanic(
        mechanicUsername,
        mechanicPassword,
        Number(mechanicParkId),
      )
      setMechanicUsername('')
      setMechanicPassword('')
    })
  }

  const saveIntegration = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      if (trackerToken.trim()) {
        await api.setTrackerToken(trackerToken.trim())
        setTrackerToken('')
      }
      if (emergencyCookie.trim()) {
        await api.setEmergencyCookie(emergencyCookie.trim())
        setEmergencyCookie('')
      }
    })
  }

  const toggleSelection = (userId: number, parkId: number) => {
    setSelections((current) => {
      const selected = current[userId] ?? []
      return {
        ...current,
        [userId]: selected.includes(parkId)
          ? selected.filter((id) => id !== parkId)
          : [...selected, parkId],
      }
    })
  }

  const editPark = (parkId: number, changes: Partial<Park>) => {
    setParks((current) => current.map((park) => (
      park.id === parkId ? { ...park, ...changes } : park
    )))
  }

  const editMechanicDraft = (
    mechanicId: number,
    changes: Partial<{ parkId: string; password: string }>,
  ) => {
    setMechanicDrafts((current) => ({
      ...current,
      [mechanicId]: { ...current[mechanicId], ...changes },
    }))
  }

  const parseOptionalInt = (value: string) => {
    const trimmed = value.trim()
    if (!trimmed) return null
    const parsed = Number(trimmed)
    return Number.isFinite(parsed) ? parsed : null
  }

  const saveMechanic = (mechanicId: number) => {
    const draft = mechanicDrafts[mechanicId]
    if (!draft) return
    return run(async () => {
      await api.updateMechanic(mechanicId, {
        park_id: Number(draft.parkId),
        ...(draft.password ? { password: draft.password } : {}),
      })
    })
  }

  const pendingAccess = accessRequests.filter(
    (request) => request.access_status === 'pending',
  )

  const parkName = (parkId: number) =>
    parks.find((park) => park.id === parkId)?.name ?? `#${parkId}`

  return (
    <PageShell
      subtitle="Парки, доступы, механики и интеграции Tracker / Emergency."
      title="Администрирование"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Секреты хранятся в базе; в интерфейсе показываются только маскированные значения." title="Интеграции">
        {settings && (
          <div className="stat-grid">
            <div className="stat">
              <span className="stat-label">Tracker OAuth</span>
              <span className="stat-value">{settings.tracker_token_masked ?? 'не задан'}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Emergency cookie</span>
              <span className="stat-value">{settings.emergency_cookie_masked ?? 'не задан'}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Статус cookie</span>
              <span className="stat-value">{cookieBadge(settings.emergency_cookie_valid)}</span>
            </div>
          </div>
        )}
        <form className="inline-form" onSubmit={saveIntegration}>
          <input
            aria-label="Tracker token"
            onChange={(event) => setTrackerToken(event.target.value)}
            placeholder="OAuth-токен Tracker"
            type="password"
            value={trackerToken}
          />
          <input
            aria-label="Emergency cookie"
            onChange={(event) => setEmergencyCookie(event.target.value)}
            placeholder="Cookie Emergency"
            type="password"
            value={emergencyCookie}
          />
          <button type="submit">Сохранить секреты</button>
        </form>
        <div className="actions" style={{ marginTop: '0.75rem' }}>
          <Link to="/admin/emergency">Открыть Emergency →</Link>
          <Link to="/admin/emergency/config">Конфиг Emergency →</Link>
        </div>
        {trackerPolicy && (
          <div className="actions" style={{ marginTop: '0.75rem' }}>
            <span>Неразмеченные для оператора: {trackerPolicy.operator_show_untagged ? 'вкл' : 'выкл'}</span>
            <span>Запись механика: {trackerPolicy.mechanic_can_write ? 'вкл' : 'выкл'}</span>
            <button
              onClick={() => run(async () => {
                await api.updateTrackerPolicy({
                  operator_show_untagged: !trackerPolicy.operator_show_untagged,
                })
              })}
              type="button"
            >
              Переключить untagged
            </button>
            <button
              onClick={() => run(async () => {
                await api.updateTrackerPolicy({
                  mechanic_can_write: !trackerPolicy.mechanic_can_write,
                })
              })}
              type="button"
            >
              Переключить запись механика
            </button>
            <Link to="/admin/tracker">Рабочий стол Tracker →</Link>
          </div>
        )}
      </Panel>

      <Panel hint="Механик получает ровно один активный парк и сразу одобренный доступ." title="Механики">
        <form className="inline-form" onSubmit={createMechanic}>
          <input
            aria-label="Логин механика"
            onChange={(event) => setMechanicUsername(event.target.value)}
            placeholder="Логин"
            required
            value={mechanicUsername}
          />
          <input
            aria-label="Пароль механика"
            onChange={(event) => setMechanicPassword(event.target.value)}
            placeholder="Пароль"
            required
            type="password"
            value={mechanicPassword}
          />
          <select
            aria-label="Парк механика"
            onChange={(event) => setMechanicParkId(event.target.value)}
            required
            value={mechanicParkId}
          >
            {parks.filter((park) => park.is_active).map((park) => (
              <option key={park.id} value={park.id}>{park.name}</option>
            ))}
          </select>
          <button type="submit">Создать механика</button>
        </form>
        {mechanics.length ? (
          <div className="table-scroll">
            <ul className="card-list">
              {mechanics.map((mechanic) => (
              <li className="card" key={mechanic.id}>
                <div className="card-title">{mechanic.username}</div>
                <div className="card-meta">
                  <Badge active={mechanic.is_active} />
                </div>
                <div className="inline-form">
                  <select
                    aria-label={`Парк для ${mechanic.username}`}
                    onChange={(event) => editMechanicDraft(mechanic.id, {
                      parkId: event.target.value,
                    })}
                    value={mechanicDrafts[mechanic.id]?.parkId ?? String(mechanic.park.id)}
                  >
                    {parks.filter((park) => park.is_active).map((park) => (
                      <option key={park.id} value={park.id}>{park.name}</option>
                    ))}
                  </select>
                  <input
                    aria-label={`Новый пароль для ${mechanic.username}`}
                    onChange={(event) => editMechanicDraft(mechanic.id, {
                      password: event.target.value,
                    })}
                    placeholder="Новый пароль (необязательно)"
                    type="password"
                    value={mechanicDrafts[mechanic.id]?.password ?? ''}
                  />
                </div>
                <div className="actions">
                  <button onClick={() => saveMechanic(mechanic.id)} type="button">
                    {ru.save}
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => run(() => api.updateMechanic(mechanic.id, {
                      is_active: !mechanic.is_active,
                    }))}
                    type="button"
                  >
                    {mechanic.is_active ? ru.deactivate : ru.activate}
                  </button>
                </div>
              </li>
              ))}
            </ul>
          </div>
        ) : (
          <EmptyState>Механики ещё не созданы.</EmptyState>
        )}
      </Panel>

      <Panel hint="Тег используется в Tracker; очередь нужна для задач и поиска." title="Парки">
        <form className="inline-form" onSubmit={createPark}>
          <input
            aria-label="Название парка"
            onChange={(event) => setName(event.target.value)}
            placeholder="Название"
            required
            value={name}
          />
          <input
            aria-label="Тег парка"
            onChange={(event) => setTag(event.target.value)}
            placeholder="Тег"
            required
            value={tag}
          />
          <button type="submit">{ru.create}</button>
        </form>
        <div className="table-scroll">
          <ul className="card-list">
            {parks.map((park) => (
              <li className="card" key={park.id}>
              <div className="inline-form">
                <input
                  aria-label={`Название парка ${park.id}`}
                  onChange={(event) => editPark(park.id, { name: event.target.value })}
                  required
                  value={park.name}
                />
                <input
                  aria-label={`Тег парка ${park.id}`}
                  onChange={(event) => editPark(park.id, { tag: event.target.value })}
                  required
                  value={park.tag}
                />
                <input
                  aria-label={`Очередь Tracker ${park.id}`}
                  onChange={(event) => editPark(park.id, { tracker_queue: event.target.value })}
                  placeholder="Очередь Tracker"
                  value={park.tracker_queue ?? ''}
                />
                <input
                  aria-label={`Приоритет Tracker ${park.id}`}
                  onChange={(event) => editPark(park.id, {
                    tracker_priority: event.target.value || null,
                  })}
                  placeholder="Приоритет (blocker)"
                  value={park.tracker_priority ?? ''}
                />
                <input
                  aria-label={`Тип Tracker ${park.id}`}
                  onChange={(event) => editPark(park.id, {
                    tracker_type: event.target.value || null,
                  })}
                  placeholder="Тип (пусто = без фильтра)"
                  value={park.tracker_type ?? ''}
                />
                <input
                  aria-label={`Group ID ${park.id}`}
                  onChange={(event) => editPark(park.id, {
                    group_id: parseOptionalInt(event.target.value),
                  })}
                  placeholder="Group ID (Telegram)"
                  value={park.group_id ?? ''}
                />
                <input
                  aria-label={`Chat ID ${park.id}`}
                  onChange={(event) => editPark(park.id, {
                    chat_id: parseOptionalInt(event.target.value),
                  })}
                  placeholder="Chat ID"
                  value={park.chat_id ?? ''}
                />
                <div className="checks">
                  <label>
                    <input
                      checked={park.feature_blockers ?? true}
                      onChange={(event) => editPark(park.id, {
                        feature_blockers: event.target.checked,
                      })}
                      type="checkbox"
                    />
                    Задачи
                  </label>
                  <label>
                    <input
                      checked={park.feature_reports ?? true}
                      onChange={(event) => editPark(park.id, {
                        feature_reports: event.target.checked,
                      })}
                      type="checkbox"
                    />
                    Отчёты
                  </label>
                  <label>
                    <input
                      checked={park.feature_sla_repair ?? true}
                      onChange={(event) => editPark(park.id, {
                        feature_sla_repair: event.target.checked,
                      })}
                      type="checkbox"
                    />
                    SLA ремонт
                  </label>
                  <label>
                    <input
                      checked={park.feature_backlog_alerts ?? true}
                      onChange={(event) => editPark(park.id, {
                        feature_backlog_alerts: event.target.checked,
                      })}
                      type="checkbox"
                    />
                    Backlog alerts
                  </label>
                </div>
                <Badge active={park.is_active ?? true} />
              </div>
              <div className="actions">
                <button
                  disabled={!park.name || !park.tag}
                  onClick={() => run(() => api.updatePark(park.id, {
                    name: park.name,
                    tag: park.tag,
                    tracker_queue: park.tracker_queue || null,
                    tracker_priority: park.tracker_priority || null,
                    tracker_type: park.tracker_type || null,
                    group_id: park.group_id ?? null,
                    chat_id: park.chat_id ?? null,
                    feature_blockers: park.feature_blockers,
                    feature_reports: park.feature_reports,
                    feature_sla_repair: park.feature_sla_repair,
                    feature_backlog_alerts: park.feature_backlog_alerts,
                  }))}
                  type="button"
                >
                  {ru.save}
                </button>
                <button
                  onClick={() => run(() => api.updatePark(park.id, {
                    is_active: !park.is_active,
                  }))}
                  type="button"
                >
                  {park.is_active ? ru.deactivate : ru.activate}
                </button>
              </div>
              </li>
            ))}
          </ul>
        </div>
      </Panel>

      <Panel hint="Одобрение требует выбора хотя бы одного активного парка." title="Заявки на доступ">
        {pendingAccess.length ? pendingAccess.map((request) => (
          <article className="inbox-item" key={request.id}>
            <strong>{request.username}</strong>
            <p className="field-hint">Выберите парки для назначения оператору.</p>
            <div className="checks">
              {parks.filter((park) => park.is_active).map((park) => (
                <label key={park.id}>
                  <input
                    checked={(selections[request.id] ?? []).includes(park.id)}
                    onChange={() => toggleSelection(request.id, park.id)}
                    type="checkbox"
                  />
                  {park.name}
                </label>
              ))}
            </div>
            <div className="actions">
              <button
                disabled={!(selections[request.id]?.length)}
                onClick={() => run(() => api.approveAccessRequest(
                  request.id,
                  selections[request.id] ?? [],
                ))}
                type="button"
              >
                {ru.approve}
              </button>
              <button
                onClick={() => run(() => api.rejectAccessRequest(request.id))}
                type="button"
              >
                {ru.reject}
              </button>
            </div>
          </article>
        )) : (
          <EmptyState>Нет заявок на первичный доступ.</EmptyState>
        )}
      </Panel>

      <Panel hint="Операторы запрашивают дополнительные парки из своего кабинета." title="Заявки на парки">
        {parkRequests.length ? (
          <ul className="card-list">
            {parkRequests.map((request) => (
              <li className="card action-row" key={request.id}>
                <div>
                  <div className="card-title">Пользователь #{request.user_id}</div>
                  <div className="card-meta">Парк: {parkName(request.park_id)}</div>
                </div>
                <div className="actions">
                  <button
                    onClick={() => run(() => api.resolveParkRequest(request.id, 'approve'))}
                    type="button"
                  >
                    {ru.approve}
                  </button>
                  <button
                    onClick={() => run(() => api.resolveParkRequest(request.id, 'reject'))}
                    type="button"
                  >
                    {ru.reject}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>Нет заявок на дополнительные парки.</EmptyState>
        )}
      </Panel>
    </PageShell>
  )
}
