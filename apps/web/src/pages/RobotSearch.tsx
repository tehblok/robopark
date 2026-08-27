import { type FormEvent, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { Alert, Panel } from '../components/PageShell'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'

function searchTickets(role: string, query: string) {
  if (role === 'mechanic') return api.mechanicRobotTickets(query)
  return api.operatorRobotTickets(query)
}

export function RobotSearch() {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [openKey, setOpenKey] = useState('')

  const trimmedSubmitted = submitted.trim()
  const searchRes = useCachedResource(
    trimmedSubmitted ? `robot:tickets:${user?.role ?? 'anon'}:${trimmedSubmitted}` : '',
    () => searchTickets(user?.role ?? 'operator', trimmedSubmitted),
    { enabled: Boolean(user && trimmedSubmitted) },
  )

  const items = searchRes.data?.items ?? []
  const errorText = searchRes.error
    ? mapApiError(searchRes.error, ru.errors.robotSearch)
    : ''

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(query.trim())
  }

  if (openKey) {
    return (
      <IssueDrawer
        canWrite
        issueKey={openKey}
        onChanged={() => {
          void searchRes.refresh()
        }}
        onClose={() => setOpenKey('')}
      />
    )
  }

  const showColdSkeleton =
    Boolean(trimmedSubmitted) && searchRes.isLoading && !searchRes.data && !errorText

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
          <button
            className="btn"
            disabled={searchRes.isLoading || !query.trim()}
            type="submit"
          >
            {searchRes.isLoading ? <Spinner label="Поиск" /> : ru.search}
          </button>
        </form>
      </Panel>

      {errorText && <Alert tone="error">{errorText}</Alert>}

      {showColdSkeleton && <SkeletonList rows={3} />}

      {!trimmedSubmitted && (
        <EmptyBlock
          hint="Поиск идёт по всем паркам, доступным вашей роли."
          icon="⌕"
          title="Введите номер робота или ключ тикета"
        />
      )}

      {trimmedSubmitted && searchRes.data && !items.length && !errorText && (
        <EmptyBlock
          hint="Проверьте номер робота или ключ задачи."
          icon="🔍"
          title="Ничего не найдено"
        />
      )}

      {items.length > 0 && (
        <Panel title={`Найдено: ${items.length}`}>
          <TaskList items={items} onSelect={setOpenKey} selected={openKey} />
        </Panel>
      )}
    </>
  )
}
