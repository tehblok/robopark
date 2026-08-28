import { type FormEvent, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { loadRecentRobots, pushRecentRobot } from '../lib/recentRobots'
import { useCachedResource } from '../lib/resource'
import { canSearchRobotTickets, searchPathForRole } from '../lib/robotSearch'

function searchTickets(role: string, query: string) {
  const path = searchPathForRole(role)
  if (path === 'mechanic') return api.mechanicRobotTickets(query)
  if (path === 'operator') return api.operatorRobotTickets(query)
  return api.trackerRobotTickets(query)
}

export function RobotSearch() {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [openKey, setOpenKey] = useState('')
  const [recent, setRecent] = useState(() => loadRecentRobots())
  const permissions = user?.permissions ?? []
  const canSearch = canSearchRobotTickets(user?.role ?? '', permissions)
  const canWrite = permissions.includes('tracker.write')

  const trimmedSubmitted = submitted.trim()
  const searchRes = useCachedResource(
    trimmedSubmitted ? `robot:tickets:${user?.role ?? 'anon'}:${trimmedSubmitted}` : '',
    () => searchTickets(user?.role ?? 'operator', trimmedSubmitted),
    { enabled: Boolean(user && trimmedSubmitted && canSearch) },
  )

  useEffect(() => {
    if (!trimmedSubmitted || !searchRes.data) return
    setRecent(pushRecentRobot(trimmedSubmitted))
  }, [searchRes.data, trimmedSubmitted])

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
        canWrite={canWrite}
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
    <PageShell
      subtitle="Тикеты Tracker по номеру робота или ключу задачи."
      title={ru.nav.robot_search}
    >
      {!canSearch && (
        <EmptyBlock
          hint="Для этой роли поиск по Tracker не включён."
          icon="⌕"
          title="Поиск недоступен"
        />
      )}

      {canSearch && (
        <Panel hint="Используется OAuth Tracker из настроек администратора." title="Запрос">
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
          {recent.length > 0 && (
            <div className="chip-row">
              {recent.map((item) => (
                <button
                  className="chip"
                  key={item}
                  onClick={() => {
                    setQuery(item)
                    setSubmitted(item)
                  }}
                  type="button"
                >
                  {item}
                </button>
              ))}
            </div>
          )}
        </Panel>
      )}

      {errorText && <Alert tone="error">{errorText}</Alert>}

      {showColdSkeleton && <SkeletonList rows={3} />}

      {canSearch && !trimmedSubmitted && (
        <EmptyBlock
          hint="Поиск идёт по очередям парков, доступных вашей роли."
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
    </PageShell>
  )
}
