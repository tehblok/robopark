import { type FormEvent, useEffect, useState } from 'react'
import {
  api,
  type AccessRequest,
  type IntegrationSettings,
  type Mechanic,
  type Park,
  type ParkRequest,
  type TrackerPolicySettings,
} from '../api'
import { useAuth } from '../auth-context'
import { Link } from 'react-router-dom'

export function Admin() {
  const { logout } = useAuth()
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
    setMechanicParkId((current) => current || String(parkList.find((park) => park.is_active)?.id ?? ''))
  }

  useEffect(() => {
    load().catch(() => setError('Could not load admin data.'))
  }, [])

  const run = async (action: () => Promise<unknown>) => {
    setError('')
    try {
      await action()
      await load()
    } catch {
      setError('The action could not be completed.')
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

  const pendingAccess = accessRequests.filter(
    (request) => request.access_status === 'pending',
  )

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Admin</h1>
          <button onClick={logout} type="button">Sign out</button>
        </header>
        <nav className="actions">
          <Link to="/admin/tracker">Tracker workspace</Link>
        </nav>

        {error && <p className="error">{error}</p>}

        <section>
          <h2>Integration settings</h2>
          {settings && (
            <p>
              Tracker: {settings.tracker_token_masked ?? 'not set'}
              {' · '}
              Emergency: {settings.emergency_cookie_masked ?? 'not set'}
              {' · '}
              Cookie valid: {String(settings.emergency_cookie_valid ?? 'unknown')}
            </p>
          )}
          <form className="inline-form" onSubmit={saveIntegration}>
            <input
              aria-label="Tracker token"
              onChange={(event) => setTrackerToken(event.target.value)}
              placeholder="Tracker OAuth token"
              type="password"
              value={trackerToken}
            />
            <input
              aria-label="Emergency cookie"
              onChange={(event) => setEmergencyCookie(event.target.value)}
              placeholder="Emergency cookie"
              type="password"
              value={emergencyCookie}
            />
            <button type="submit">Save secrets</button>
          </form>
          {trackerPolicy && (
            <div className="actions">
              <span>Operator untagged: {trackerPolicy.operator_show_untagged ? 'on' : 'off'}</span>
              <span>Mechanic write: {trackerPolicy.mechanic_can_write ? 'on' : 'off'}</span>
              <button
                onClick={() => run(async () => {
                  await api.updateTrackerPolicy({
                    operator_show_untagged: !trackerPolicy.operator_show_untagged,
                  })
                })}
                type="button"
              >
                Toggle untagged
              </button>
              <button
                onClick={() => run(async () => {
                  await api.updateTrackerPolicy({
                    mechanic_can_write: !trackerPolicy.mechanic_can_write,
                  })
                })}
                type="button"
              >
                Toggle mechanic write
              </button>
            </div>
          )}
        </section>

        <section>
          <h2>Mechanics</h2>
          <form className="inline-form" onSubmit={createMechanic}>
            <input
              aria-label="Mechanic username"
              onChange={(event) => setMechanicUsername(event.target.value)}
              placeholder="Username"
              required
              value={mechanicUsername}
            />
            <input
              aria-label="Mechanic password"
              onChange={(event) => setMechanicPassword(event.target.value)}
              placeholder="Password"
              required
              type="password"
              value={mechanicPassword}
            />
            <select
              aria-label="Mechanic park"
              onChange={(event) => setMechanicParkId(event.target.value)}
              required
              value={mechanicParkId}
            >
              {parks.filter((park) => park.is_active).map((park) => (
                <option key={park.id} value={park.id}>{park.name}</option>
              ))}
            </select>
            <button type="submit">Create mechanic</button>
          </form>
          <ul>
            {mechanics.map((mechanic) => (
              <li className="action-row" key={mechanic.id}>
                <span>{mechanic.username}</span>
                <span>{mechanic.park.name}</span>
                <span>{mechanic.is_active ? 'active' : 'inactive'}</span>
                <button
                  onClick={() => run(() => api.updateMechanic(mechanic.id, {
                    is_active: !mechanic.is_active,
                  }))}
                  type="button"
                >
                  {mechanic.is_active ? 'Deactivate' : 'Activate'}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2>Parks</h2>
          <form className="inline-form" onSubmit={createPark}>
            <input
              aria-label="Park name"
              onChange={(event) => setName(event.target.value)}
              placeholder="Name"
              required
              value={name}
            />
            <input
              aria-label="Park tag"
              onChange={(event) => setTag(event.target.value)}
              placeholder="Tag"
              required
              value={tag}
            />
            <button type="submit">Create</button>
          </form>
          <ul>
            {parks.map((park) => (
              <li className="action-row" key={park.id}>
                <input
                  aria-label={`Park ${park.id} name`}
                  onChange={(event) => editPark(park.id, { name: event.target.value })}
                  required
                  value={park.name}
                />
                <input
                  aria-label={`Park ${park.id} tag`}
                  onChange={(event) => editPark(park.id, { tag: event.target.value })}
                  required
                  value={park.tag}
                />
                <input
                  aria-label={`Park ${park.id} tracker queue`}
                  onChange={(event) => editPark(park.id, { tracker_queue: event.target.value })}
                  placeholder="Tracker queue"
                  value={park.tracker_queue ?? ''}
                />
                <label>
                  <input
                    checked={park.feature_blockers ?? true}
                    onChange={(event) => editPark(park.id, {
                      feature_blockers: event.target.checked,
                    })}
                    type="checkbox"
                  />
                  Blockers
                </label>
                <span>{park.is_active ? 'active' : 'inactive'}</span>
                <button
                  disabled={!park.name || !park.tag}
                  onClick={() => run(() => api.updatePark(park.id, {
                    name: park.name,
                    tag: park.tag,
                    tracker_queue: park.tracker_queue || null,
                    feature_blockers: park.feature_blockers,
                  }))}
                  type="button"
                >
                  Save
                </button>
                <button
                  onClick={() => run(() => api.updatePark(park.id, {
                    is_active: !park.is_active,
                  }))}
                  type="button"
                >
                  {park.is_active ? 'Deactivate' : 'Activate'}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2>Access requests</h2>
          {pendingAccess.length ? pendingAccess.map((request) => (
            <article className="inbox-item" key={request.id}>
              <strong>{request.username}</strong>
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
                  Approve
                </button>
                <button
                  onClick={() => run(() => api.rejectAccessRequest(request.id))}
                  type="button"
                >
                  Reject
                </button>
              </div>
            </article>
          )) : <p>No pending access requests.</p>}
        </section>

        <section>
          <h2>Park requests</h2>
          {parkRequests.length ? (
            <ul>
              {parkRequests.map((request) => (
                <li className="action-row" key={request.id}>
                  <span>User #{request.user_id} requests park #{request.park_id}</span>
                  <div className="actions">
                    <button
                      onClick={() => run(() => api.resolveParkRequest(request.id, 'approve'))}
                      type="button"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => run(() => api.resolveParkRequest(request.id, 'reject'))}
                      type="button"
                    >
                      Reject
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : <p>No pending park requests.</p>}
        </section>
      </section>
    </main>
  )
}
