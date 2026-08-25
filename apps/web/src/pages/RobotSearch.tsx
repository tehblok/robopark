import { type FormEvent, useState } from 'react'
import { api, type Blocker } from '../api'
import { useAuth } from '../auth-context'
import { Alert, Panel } from '../components/PageShell'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'

function searchTickets(role: string, query: string) {
  if (role === 'mechanic') return api.mechanicRobotTickets(query)
  return api.operatorRobotTickets(query)
}

export function RobotSearch() {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<Blocker[]>([])
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  const [loading, setLoading] = useState(false)
  const [openKey, setOpenKey] = useState('')

  const runSearch = async () => {
    if (!user) return
    setError('')
    setLoading(true)
    setSearched(true)
    try {
      const data = await searchTickets(user.role, query.trim())
      setItems(data.items)
    } catch (caught) {
      setItems([])
      setError(mapApiError(caught, ru.errors.robotSearch))
    } finally {
      setLoading(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    await runSearch()
  }

  // Found tickets open as a full card, just like on the tasks page.
  if (openKey) {
    return (
      <IssueDrawer
        canWrite
        issueKey={openKey}
        onChanged={() => void runSearch()}
        onClose={() => setOpenKey('')}
      />
    )
  }

  return (
    <>
      <Panel hint="Используется OAuth Tracker из настроек администратора." title="Поиск по роботу">
        <p className="panel-hint">
          Номер робота (447, a1517) или ключ тикета (ROBOPARK-123). Результаты без фильтра
          по парку.
        </p>
        <form className="search-form" onSubmit={submit}>
          <input
            aria-label="Номер робота или ключ тикета"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="447 или ROBOPARK-123"
            required
            value={query}
          />
          <button className="btn" disabled={loading || !query.trim()} type="submit">
            {loading ? <Spinner label="Поиск" /> : ru.search}
          </button>
        </form>
      </Panel>

      {error && <Alert tone="error">{error}</Alert>}

      {loading && <SkeletonList rows={3} />}

      {!loading && !searched && (
        <EmptyBlock
          hint="Поиск идёт по всем паркам, доступным вашей роли."
          icon="⌕"
          title="Введите номер робота или ключ тикета"
        />
      )}

      {!loading && searched && !items.length && !error && (
        <EmptyBlock
          hint="Проверьте номер робота или ключ задачи."
          icon="🔍"
          title="Ничего не найдено"
        />
      )}

      {!loading && items.length > 0 && (
        <Panel title={`Найдено: ${items.length}`}>
          <TaskList items={items} onSelect={setOpenKey} selected={openKey} />
        </Panel>
      )}
    </>
  )
}
