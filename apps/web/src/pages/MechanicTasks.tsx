import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Blocker } from '../api'

const FILTERS = [
  ['all', 'All'],
  ['moving', 'Moving'],
  ['queued', 'Queued'],
  ['waiting_team', 'Waiting team'],
  ['waiting_parts', 'Waiting parts'],
  ['other', 'Other'],
] as const

export function MechanicTasks() {
  const [status, setStatus] = useState('all')
  const [items, setItems] = useState<Blocker[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    api.mechanicTasks(status)
      .then((data) => {
        setItems(data.items)
        setCounts(data.counts)
      })
      .catch(() => setError('Could not load tasks. Check Tracker token and park settings.'))
  }, [status])

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Tasks</h1>
          <Link to="/mechanic">Back</Link>
        </header>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {FILTERS.map(([value, label]) => (
            <button
              key={value}
              onClick={() => setStatus(value)}
              type="button"
            >
              {label} ({counts[value] ?? 0})
            </button>
          ))}
        </div>
        <ul>
          {items.map((item) => (
            <li key={item.key}>
              <a href={item.url} rel="noreferrer" target="_blank">{item.key}</a>
              {' '}
              {item.summary}
              {' '}
              <span>{item.status}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
