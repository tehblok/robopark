import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Blocker } from '../api'

export function MechanicRobotSearch() {
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<Blocker[]>([])
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      const data = await api.mechanicRobotTickets(query.trim())
      setItems(data.items)
    } catch {
      setError('Search failed. Check Tracker token configuration.')
    }
  }

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Robot search</h1>
          <Link to="/mechanic">Back</Link>
        </header>
        <form className="inline-form" onSubmit={submit}>
          <input
            aria-label="Robot number or ticket key"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="447 or ROBOPARK-123"
            required
            value={query}
          />
          <button type="submit">Search</button>
        </form>
        {error && <p className="error">{error}</p>}
        <ul>
          {items.map((item) => (
            <li key={item.key}>
              <a href={item.url} rel="noreferrer" target="_blank">{item.key}</a>
              {' '}
              {item.summary}
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
