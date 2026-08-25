import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Blocker, type Report } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import {
  formatReportDate,
  reportKindText,
  reportStatusText,
  statusBadgeClass,
} from '../components/reports/report-utils'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskFilterBar, TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'

export function MechanicTasks() {
  const [status, setStatus] = useState('all')
  const [items, setItems] = useState<Blocker[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [parkTag, setParkTag] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [returnedReports, setReturnedReports] = useState<Report[]>([])
  const [openKey, setOpenKey] = useState('')

  useEffect(() => {
    api
      .reportsMine()
      .then((reports) => {
        setReturnedReports(reports.filter((report) => report.status === 'returned'))
      })
      .catch(() => setReturnedReports([]))
  }, [])

  const load = useCallback(
    (nextStatus: string) => {
      setLoading(true)
      setError('')
      return api
        .mechanicTasks(nextStatus)
        .then((data) => {
          setItems(data.items)
          setCounts(data.counts)
          setParkTag(data.park_tag)
        })
        .catch((loadError) => {
          setError(mapApiError(loadError, ru.errors.tasks))
        })
        .finally(() => setLoading(false))
    },
    [],
  )

  useEffect(() => {
    void load(status)
  }, [status, load])

  // The ticket card replaces the list while open — same as the Tracker layout.
  if (openKey) {
    return (
      <PageShell subtitle={`Парк ${parkTag || '…'}`} title="Задача">
        <IssueDrawer
          canWrite
          issueKey={openKey}
          onChanged={() => void load(status)}
          onClose={() => setOpenKey('')}
        />
      </PageShell>
    )
  }

  return (
    <PageShell
      subtitle={`Блокеры парка ${parkTag || '…'} · старые сверху`}
      title="Задачи парка"
    >
      {error && <Alert tone="error">{error}</Alert>}

      {returnedReports.length > 0 && (
        <Panel title={`Возвращённые репорты (${returnedReports.length})`}>
          <p className="panel-hint">
            Оператор вернул репорт на доработку. Подробности — на странице{' '}
            <Link to="/reports">Репорты</Link>.
          </p>
          <ul className="card-list">
            {returnedReports.map((report) => (
              <li className="card" key={report.id}>
                <div className="card-title">{report.title}</div>
                {report.return_comment && <p>{report.return_comment}</p>}
                <div className="card-meta">
                  <span className={statusBadgeClass(report.status)}>
                    {reportStatusText(report.status)}
                  </span>
                  <span>{reportKindText(report.kind)}</span>
                  {report.tracker_key && <span>{report.tracker_key}</span>}
                  <span>{formatReportDate(report.updated_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

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
    </PageShell>
  )
}
