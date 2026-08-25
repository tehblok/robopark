import { type FormEvent, useCallback, useEffect, useState } from 'react'
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
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { TabPanel, Tabs, Toggle } from '../components/ui/Tabs'
import { ru } from '../i18n/ru'

type TabId = 'integrations' | 'parks' | 'mechanics' | 'requests'

function cookieBadge(valid: boolean | null | undefined) {
  if (valid === true) return <span className="badge badge-ok">cookie действует</span>
  if (valid === false) return <span className="badge badge-danger">cookie недействителен</span>
  return <span className="badge badge-warn">не проверялся</span>
}

export function Admin() {
  const [tab, setTab] = useState<TabId>('integrations')
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
    Record<number, { parkId: string; password: string; trackerLogin: string }>
  >({})
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
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
    setMechanicDrafts(
      Object.fromEntries(
        mechanicList.map((mechanic) => [
          mechanic.id,
          {
            parkId: String(mechanic.park.id),
            password: '',
            trackerLogin: mechanic.tracker_login ?? '',
          },
        ]),
      ),
    )
    setMechanicParkId(
      (current) => current || String(parkList.find((park) => park.is_active)?.id ?? ''),
    )
  }, [])

  useEffect(() => {
    load()
      .catch(() => setError(ru.errors.load))
      .finally(() => setLoading(false))
  }, [load])

  const run = async (action: () => Promise<unknown>, message = 'Сохранено') => {
    setError('')
    setSuccess('')
    setBusy(true)
    try {
      await action()
      await load()
      setSuccess(message)
    } catch {
      setError(ru.errors.generic)
    } finally {
      setBusy(false)
    }
  }

  const createPark = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createPark({ name, tag })
      setName('')
      setTag('')
    }, 'Парк создан')
  }

  const createMechanic = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createMechanic(mechanicUsername, mechanicPassword, Number(mechanicParkId))
      setMechanicUsername('')
      setMechanicPassword('')
    }, 'Механик создан')
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
    }, 'Секреты обновлены')
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
    setParks((current) =>
      current.map((park) => (park.id === parkId ? { ...park, ...changes } : park)),
    )
  }

  const editMechanicDraft = (
    mechanicId: number,
    changes: Partial<{ parkId: string; password: string; trackerLogin: string }>,
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
        tracker_login: draft.trackerLogin.trim() || null,
        ...(draft.password ? { password: draft.password } : {}),
      })
    }, 'Механик обновлён')
  }

  const pendingAccess = accessRequests.filter(
    (request) => request.access_status === 'pending',
  )
  const requestsCount = pendingAccess.length + parkRequests.length
  const activeParks = parks.filter((park) => park.is_active)

  const parkName = (parkId: number) =>
    parks.find((park) => park.id === parkId)?.name ?? `#${parkId}`

  if (loading) {
    return (
      <PageShell subtitle="Загрузка данных…" title="Администрирование">
        <SkeletonList rows={4} />
      </PageShell>
    )
  }

  return (
    <PageShell
      actions={busy ? <Spinner label="Сохранение" /> : undefined}
      subtitle="Парки, доступы, механики и интеграции Tracker / Emergency."
      title="Администрирование"
    >
      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <Tabs
        items={[
          { id: 'integrations', label: 'Интеграции' },
          { id: 'parks', label: 'Парки', count: parks.length },
          { id: 'mechanics', label: 'Механики', count: mechanics.length },
          { id: 'requests', label: 'Заявки', count: requestsCount },
        ]}
        onChange={(id) => setTab(id as TabId)}
        value={tab}
      />

      {/* --- Integrations ------------------------------------------------- */}
      <TabPanel active={tab === 'integrations'}>
        <Panel
          hint="Секреты хранятся зашифрованными; в интерфейсе видно только маску."
          title="Секреты"
        >
          {settings && (
            <div className="stat-grid">
              <div className="stat">
                <span className="stat-label">Tracker OAuth</span>
                <span className="stat-value">
                  {settings.tracker_token_masked ?? 'не задан'}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Emergency cookie</span>
                <span className="stat-value">
                  {settings.emergency_cookie_masked ?? 'не задан'}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Статус cookie</span>
                <span className="stat-value">
                  {cookieBadge(settings.emergency_cookie_valid)}
                </span>
              </div>
            </div>
          )}

          <form className="form-grid" onSubmit={saveIntegration}>
            <label className="field">
              <span className="field-label">Tracker OAuth-токен</span>
              <input
                onChange={(event) => setTrackerToken(event.target.value)}
                placeholder="Оставьте пустым, чтобы не менять"
                type="password"
                value={trackerToken}
              />
            </label>
            <label className="field">
              <span className="field-label">Emergency cookie</span>
              <input
                onChange={(event) => setEmergencyCookie(event.target.value)}
                placeholder="Оставьте пустым, чтобы не менять"
                type="password"
                value={emergencyCookie}
              />
            </label>
            <div className="form-actions">
              <button
                className="btn"
                disabled={busy || (!trackerToken.trim() && !emergencyCookie.trim())}
                type="submit"
              >
                Сохранить секреты
              </button>
            </div>
          </form>
        </Panel>

        {trackerPolicy && (
          <Panel hint="Влияет на то, что видят операторы и механики." title="Политика Tracker">
            <div className="toggle-list">
              <Toggle
                checked={trackerPolicy.operator_show_untagged}
                disabled={busy}
                label="Оператор видит неразмеченные тикеты"
                onChange={(next) =>
                  run(
                    () => api.updateTrackerPolicy({ operator_show_untagged: next }),
                    'Политика обновлена',
                  )
                }
              />
              <Toggle
                checked={trackerPolicy.mechanic_can_write}
                disabled={busy}
                label="Механик может писать в Tracker"
                onChange={(next) =>
                  run(
                    () => api.updateTrackerPolicy({ mechanic_can_write: next }),
                    'Политика обновлена',
                  )
                }
              />
            </div>
          </Panel>
        )}

        <Panel title="Быстрые переходы">
          <div className="link-row">
            <Link className="btn btn-secondary" to="/admin/tracker">
              Рабочий стол Tracker
            </Link>
            <Link className="btn btn-secondary" to="/emergency">
              Emergency
            </Link>
            <Link className="btn btn-secondary" to="/admin/emergency/config">
              Конфиг Emergency
            </Link>
          </div>
        </Panel>
      </TabPanel>

      {/* --- Parks --------------------------------------------------------- */}
      <TabPanel active={tab === 'parks'}>
        <Panel hint="Тег используется в Tracker; очередь нужна для задач и поиска." title="Новый парк">
          <form className="form-grid" onSubmit={createPark}>
            <label className="field">
              <span className="field-label">Название</span>
              <input
                onChange={(event) => setName(event.target.value)}
                required
                value={name}
              />
            </label>
            <label className="field">
              <span className="field-label">Тег</span>
              <input
                onChange={(event) => setTag(event.target.value)}
                required
                value={tag}
              />
            </label>
            <div className="form-actions">
              <button className="btn" disabled={busy} type="submit">
                {ru.create}
              </button>
            </div>
          </form>
        </Panel>

        {parks.length === 0 ? (
          <EmptyBlock
            hint="Парк нужен, чтобы назначать операторов и механиков."
            icon="🏭"
            title="Парков пока нет"
          />
        ) : (
          parks.map((park) => (
            <Panel
              actions={<Badge active={park.is_active ?? true} />}
              key={park.id}
              title={park.name || `Парк #${park.id}`}
            >
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">Название</span>
                  <input
                    onChange={(event) => editPark(park.id, { name: event.target.value })}
                    required
                    value={park.name}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Тег</span>
                  <input
                    onChange={(event) => editPark(park.id, { tag: event.target.value })}
                    required
                    value={park.tag}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Очередь Tracker</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_queue: event.target.value })
                    }
                    placeholder="SDCFLEETOPS"
                    value={park.tracker_queue ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Приоритет</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_priority: event.target.value || null })
                    }
                    placeholder="blocker"
                    value={park.tracker_priority ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Тип</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_type: event.target.value || null })
                    }
                    placeholder="пусто = без фильтра"
                    value={park.tracker_type ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Group ID (Telegram)</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { group_id: parseOptionalInt(event.target.value) })
                    }
                    value={park.group_id ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Chat ID</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { chat_id: parseOptionalInt(event.target.value) })
                    }
                    value={park.chat_id ?? ''}
                  />
                </label>
              </div>

              <div className="toggle-list">
                <Toggle
                  checked={park.feature_blockers ?? true}
                  label="Задачи"
                  onChange={(next) => editPark(park.id, { feature_blockers: next })}
                />
                <Toggle
                  checked={park.feature_reports ?? true}
                  label="Отчёты"
                  onChange={(next) => editPark(park.id, { feature_reports: next })}
                />
                <Toggle
                  checked={park.feature_sla_repair ?? true}
                  label="SLA ремонт"
                  onChange={(next) => editPark(park.id, { feature_sla_repair: next })}
                />
                <Toggle
                  checked={park.feature_backlog_alerts ?? true}
                  label="Backlog alerts"
                  onChange={(next) => editPark(park.id, { feature_backlog_alerts: next })}
                />
              </div>

              <div className="form-actions">
                <button
                  className="btn"
                  disabled={!park.name || !park.tag || busy}
                  onClick={() =>
                    run(() =>
                      api.updatePark(park.id, {
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
                      }),
                    )
                  }
                  type="button"
                >
                  {ru.save}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() =>
                    run(() => api.updatePark(park.id, { is_active: !park.is_active }))
                  }
                  type="button"
                >
                  {park.is_active ? ru.deactivate : ru.activate}
                </button>
              </div>
            </Panel>
          ))
        )}
      </TabPanel>

      {/* --- Mechanics ----------------------------------------------------- */}
      <TabPanel active={tab === 'mechanics'}>
        <Panel
          hint="Механик получает ровно один активный парк и сразу одобренный доступ."
          title="Новый механик"
        >
          <form className="form-grid" onSubmit={createMechanic}>
            <label className="field">
              <span className="field-label">Логин</span>
              <input
                onChange={(event) => setMechanicUsername(event.target.value)}
                required
                value={mechanicUsername}
              />
            </label>
            <label className="field">
              <span className="field-label">Пароль</span>
              <input
                onChange={(event) => setMechanicPassword(event.target.value)}
                required
                type="password"
                value={mechanicPassword}
              />
              <span className="field-hint">
                Минимум 12 символов и три типа символов.
              </span>
            </label>
            <label className="field">
              <span className="field-label">Парк</span>
              <select
                onChange={(event) => setMechanicParkId(event.target.value)}
                required
                value={mechanicParkId}
              >
                {activeParks.map((park) => (
                  <option key={park.id} value={park.id}>
                    {park.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-actions">
              <button className="btn" disabled={busy || !activeParks.length} type="submit">
                Создать механика
              </button>
            </div>
          </form>
        </Panel>

        {mechanics.length === 0 ? (
          <EmptyBlock icon="🔧" title="Механики ещё не созданы" />
        ) : (
          mechanics.map((mechanic) => (
            <Panel
              actions={<Badge active={mechanic.is_active} />}
              key={mechanic.id}
              title={mechanic.username}
            >
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">Парк</span>
                  <select
                    onChange={(event) =>
                      editMechanicDraft(mechanic.id, { parkId: event.target.value })
                    }
                    value={mechanicDrafts[mechanic.id]?.parkId ?? String(mechanic.park.id)}
                  >
                    {activeParks.map((park) => (
                      <option key={park.id} value={park.id}>
                        {park.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">Startrek-логин</span>
                  <input
                    onChange={(event) =>
                      editMechanicDraft(mechanic.id, { trackerLogin: event.target.value })
                    }
                    placeholder="ivan.petrov"
                    value={mechanicDrafts[mechanic.id]?.trackerLogin ?? ''}
                  />
                  <span className="field-hint">
                    Нужен для фильтра «Мои» и назначения «На себя» в Tracker.
                  </span>
                </label>
                <label className="field">
                  <span className="field-label">Новый пароль</span>
                  <input
                    onChange={(event) =>
                      editMechanicDraft(mechanic.id, { password: event.target.value })
                    }
                    placeholder="необязательно"
                    type="password"
                    value={mechanicDrafts[mechanic.id]?.password ?? ''}
                  />
                  <span className="field-hint">
                    Смена пароля завершит активные сессии механика.
                  </span>
                </label>
              </div>

              <div className="toggle-list">
                <Toggle
                  checked={mechanic.must_change_password ?? false}
                  disabled={busy}
                  label="Запросить смену пароля при входе"
                  onChange={(next) =>
                    run(
                      () => api.updateMechanic(mechanic.id, { must_change_password: next }),
                      'Флаг смены пароля обновлён',
                    )
                  }
                />
              </div>

              <div className="form-actions">
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => saveMechanic(mechanic.id)}
                  type="button"
                >
                  {ru.save}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() =>
                    run(() =>
                      api.updateMechanic(mechanic.id, { is_active: !mechanic.is_active }),
                    )
                  }
                  type="button"
                >
                  {mechanic.is_active ? ru.deactivate : ru.activate}
                </button>
              </div>
            </Panel>
          ))
        )}
      </TabPanel>

      {/* --- Requests ------------------------------------------------------ */}
      <TabPanel active={tab === 'requests'}>
        <Panel
          hint="Одобрение требует выбора хотя бы одного активного парка."
          title={`Заявки на доступ (${pendingAccess.length})`}
        >
          {pendingAccess.length === 0 ? (
            <EmptyBlock icon="✅" title="Нет заявок на первичный доступ" />
          ) : (
            pendingAccess.map((request) => (
              <article className="inbox-item" key={request.id}>
                <strong>{request.username}</strong>
                <p className="field-hint">Выберите парки для назначения оператору.</p>
                <div className="checks">
                  {activeParks.map((park) => (
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
                <div className="form-actions">
                  <button
                    className="btn"
                    disabled={!selections[request.id]?.length || busy}
                    onClick={() =>
                      run(
                        () =>
                          api.approveAccessRequest(request.id, selections[request.id] ?? []),
                        'Доступ одобрен',
                      )
                    }
                    type="button"
                  >
                    {ru.approve}
                  </button>
                  <button
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() =>
                      run(() => api.rejectAccessRequest(request.id), 'Заявка отклонена')
                    }
                    type="button"
                  >
                    {ru.reject}
                  </button>
                </div>
              </article>
            ))
          )}
        </Panel>

        <Panel
          hint="Операторы запрашивают дополнительные парки из своего кабинета."
          title={`Заявки на парки (${parkRequests.length})`}
        >
          {parkRequests.length === 0 ? (
            <EmptyBlock icon="✅" title="Нет заявок на дополнительные парки" />
          ) : (
            <ul className="card-list">
              {parkRequests.map((request) => (
                <li className="card action-row" key={request.id}>
                  <div>
                    <div className="card-title">Пользователь #{request.user_id}</div>
                    <div className="card-meta">Парк: {parkName(request.park_id)}</div>
                  </div>
                  <div className="form-actions">
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => api.resolveParkRequest(request.id, 'approve'),
                          'Заявка одобрена',
                        )
                      }
                      type="button"
                    >
                      {ru.approve}
                    </button>
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => api.resolveParkRequest(request.id, 'reject'),
                          'Заявка отклонена',
                        )
                      }
                      type="button"
                    >
                      {ru.reject}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </TabPanel>
    </PageShell>
  )
}
