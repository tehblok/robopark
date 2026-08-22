import { type FormEvent, useEffect, useState } from 'react'
import {
  api,
  type AccessRequest,
  type Park,
  type ParkRequest,
} from '../api'
import { useAuth } from '../auth-context'

export function Admin() {
  const { logout } = useAuth()
  const [parks, setParks] = useState<Park[]>([])
  const [accessRequests, setAccessRequests] = useState<AccessRequest[]>([])
  const [parkRequests, setParkRequests] = useState<ParkRequest[]>([])
  const [selections, setSelections] = useState<Record<number, number[]>>({})
  const [name, setName] = useState('')
  const [tag, setTag] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    const [parkList, accessInbox, parkInbox] = await Promise.all([
      api.parks(),
      api.accessRequests(),
      api.adminParkRequests(),
    ])
    setParks(parkList)
    setAccessRequests(accessInbox)
    setParkRequests(parkInbox)
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
      await api.createPark(name, tag)
      setName('')
      setTag('')
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

  const editPark = (parkId: number, changes: Partial<Pick<Park, 'name' | 'tag'>>) => {
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

        {error && <p className="error">{error}</p>}

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
                <span>{park.is_active ? 'active' : 'inactive'}</span>
                <button
                  disabled={!park.name || !park.tag}
                  onClick={() => run(() => api.updatePark(park.id, {
                    name: park.name,
                    tag: park.tag,
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
