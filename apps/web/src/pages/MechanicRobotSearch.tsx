import { type FormEvent, useState } from 'react'
import { api, type Blocker } from '../api'
import { Alert, EmptyState, PageShell, Panel } from '../components/PageShell'
import { ru } from '../i18n/ru'

export function MechanicRobotSearch() {
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<Blocker[]>([])
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setSearched(true)
    try {
      const data = await api.mechanicRobotTickets(query.trim())
      setItems(data.items)
    } catch (caught) {
      const code = caught instanceof Error ? caught.message : ''
      setError(code === '503' ? ru.errors.tasks503 : ru.errors.robotSearch)
    }
  }

  return (
    <PageShell
      backTo="/mechanic"
      subtitle="Поиск по номеру робота (447, a1517) или ключу тикета (ROBOPARK-123). Результаты без фильтра по парку."
      title="Поиск робота"
    >
      <Panel hint="Используется OAuth Tracker из настроек администратора." title="Запрос">
        <form className="inline-form" onSubmit={submit}>
          <input
            aria-label="Номер робота или ключ тикета"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="447 или ROBOPARK-123"
            required
            value={query}
          />
          <button type="submit">{ru.search}</button>
        </form>
      </Panel>

      {error && <Alert tone="error">{error}</Alert>}

      <Panel title="Результаты">
        {searched && !items.length && !error && (
          <EmptyState>Ничего не найдено. Проверьте номер или ключ задачи.</EmptyState>
        )}
        {!searched && <EmptyState>Введите запрос и нажмите «Найти».</EmptyState>}
        {items.length > 0 && (
          <ul className="card-list">
            {items.map((item) => (
              <li className="card" key={item.key}>
                <div className="card-title">
                  <a href={item.url} rel="noreferrer" target="_blank">{item.key}</a>
                </div>
                <p>{item.summary}</p>
                <div className="card-meta">
                  <span>Статус: {item.status}</span>
                  {item.robot && <span>Робот: {item.robot}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </PageShell>
  )
}
