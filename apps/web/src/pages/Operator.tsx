import { type FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Park, type ParkRequest } from '../api'
import { useAuth } from '../auth-context'

export function Operator() {
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
    load().catch(() => setError('Could not load operator data.'))
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      await api.requestPark(Number(parkId))
      setParkId('')
      await load()
    } catch {
      setError('Could not submit park request.')
    }
  }

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Operator</h1>
          <button onClick={logout} type="button">Sign out</button>
        </header>
        <nav className="actions">
          <Link to="/operator/blockers">Blockers</Link>
          <Link to="/operator/robot-search">Robot search</Link>
          <Link to="/operator/now-report">Now report</Link>
          <Link to="/operator/tracker">Tracker workspace</Link>
        </nav>

        {error && <p className="error">{error}</p>}

        <section>
          <h2>My parks</h2>
          {parks.length ? (
            <ul>{parks.map((park) => <li key={park.id}>{park.name} ({park.tag})</li>)}</ul>
          ) : <p>No assigned parks.</p>}
        </section>

        <section>
          <h2>Request a park</h2>
          <form className="inline-form" onSubmit={submit}>
            <select
              aria-label="Park"
              disabled={!available.length}
              onChange={(event) => setParkId(event.target.value)}
              value={parkId}
            >
              {available.map((park) => (
                <option key={park.id} value={park.id}>{park.name} ({park.tag})</option>
              ))}
            </select>
            <button disabled={!parkId} type="submit">Request access</button>
          </form>
          {!available.length && <p>No parks are available to request.</p>}
        </section>

        <section>
          <h2>My requests</h2>
          {requests.length ? (
            <ul>
              {requests.map((request) => (
                <li key={request.id}>Park #{request.park_id}: {request.status}</li>
              ))}
            </ul>
          ) : <p>No park requests.</p>}
        </section>
      </section>
    </main>
  )
}
