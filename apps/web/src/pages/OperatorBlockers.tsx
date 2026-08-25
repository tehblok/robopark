import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Blocker, type Park } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskFilterBar, TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'

export function OperatorBlockers() {
  const [parks, setParks] = useState<Park[]>([])
  const [parkId, setParkId] = useState<number | null>(null)
  const [status, setStatus] = useState('all')
  const [items, setItems] = useState<Blocker[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [parkTag, setParkTag] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [openKey, setOpenKey] = useState('')
  const requestIdRef = useRef(0)

  useEffect(() => {
    api
      .operatorParks()
      .then((data) => {
        setParks(data)
        setParkId((current) => current ?? data[0]?.id ?? null)
      })
      .catch((loadError) => {
        setError(mapApiError(loadError, ru.errors.load))
        setLoading(false)
      })
  }, [])

  const load = useCallback((park: number | null, nextStatus: string) => {
    if (park == null) {
      setLoading(false)
      return
    }
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    api
      .operatorBlockers(park, nextStatus)
      .then((data) => {
        if (requestId !== requestIdRef.current) return
        setItems(data.items)
        setCounts(data.counts)
        setParkTag(data.park_tag)
      })
      .catch((loadError) => {
        if (requestId !== requestIdRef.current) return
        setError(mapApiError(loadError, ru.errors.tasks))
        setItems([])
        setCounts({})
      })
      .finally(() => {
        if (requestId === requestIdRef.current) {
          setLoading(false)
        }
      })
  }, [])

  useEffect(() => {
    load(parkId, status)
  }, [parkId, status, load])

  if (openKey) {
    return (
      <PageShell subtitle={`Парк ${parkTag || '…'}`} title="Задача">
        <IssueDrawer
          canWrite
          issueKey={openKey}
          onChanged={() => load(parkId, status)}
          onClose={() => setOpenKey('')}
        />
      </PageShell>
    )
  }

  return (
    <PageShell
      subtitle={`Блокеры парка ${parkTag || '…'} · старые сверху`}
      title="Блокеры"
    >
      {error && <Alert tone="error">{error}</Alert>}

      {parks.length > 1 && (
        <Panel hint="Задачи показываются по выбранному парку." title="Парк">
          <select
            aria-label="Парк"
            className="park-select"
            onChange={(event) => setParkId(Number(event.target.value))}
            value={parkId ?? ''}
          >
            {parks.map((park) => (
              <option key={park.id} value={park.id}>
                {park.name} ({park.tag})
              </option>
            ))}
          </select>
        </Panel>
      )}

      {!parks.length && !error && (
        <EmptyBlock
          action={
            <Link className="btn btn-secondary" to="/operator/parks">
              Мои парки
            </Link>
          }
          hint="Запросите доступ на странице «Мои парки»."
          icon="🏭"
          title="Нет назначенных парков"
        />
      )}

      {parks.length > 0 && (
        <>
          <TaskFilterBar counts={counts} onChange={setStatus} value={status} />

          {loading && <SkeletonList rows={4} />}
          {!loading && !items.length && !error && (
            <EmptyBlock
              hint="Смените фильтр статуса или обновите список позже."
              icon="📋"
              title="Нет открытых блокеров для выбранного фильтра"
            />
          )}
          {!loading && items.length > 0 && (
            <TaskList items={items} onSelect={setOpenKey} selected={openKey} />
          )}
        </>
      )}
    </PageShell>
  )
}
