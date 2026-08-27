import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Report } from '../api'
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
import { useCachedResource } from '../lib/resource'

export function MechanicTasks() {
  const [status, setStatus] = useState('all')
  const [openKey, setOpenKey] = useState('')

  const tasksRes = useCachedResource(
    `mechanic:tasks:${status}`,
    () => api.mechanicTasks(status),
  )
  const items = tasksRes.data?.items ?? []
  const counts = tasksRes.data?.counts ?? {}
  const parkTag = tasksRes.data?.park_tag ?? ''

  const reportsRes = useCachedResource<Report[]>(
    'mechanic:reports:mine',
    () => api.reportsMine(),
  )
  const returnedReports = (reportsRes.data ?? []).filter(
    (report) => report.status === 'returned',
  )

  const errorText = tasksRes.error ? mapApiError(tasksRes.error, ru.errors.tasks) : ''
  const showColdSkeleton = tasksRes.isLoading && !tasksRes.data && !errorText

  // The ticket card replaces the list while open — same as the Tracker layout.
  if (openKey) {
    return (
      <PageShell subtitle={`Парк ${parkTag || '…'}`} title="Задача">
        <IssueDrawer
          canWrite
          issueKey={openKey}
          onChanged={() => {
            void tasksRes.refresh()
          }}
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
      {errorText && <Alert tone="error">{errorText}</Alert>}

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

      {showColdSkeleton && <SkeletonList rows={4} />}
      {!showColdSkeleton && !items.length && !errorText && tasksRes.data && (
        <EmptyBlock
          hint="Смените фильтр статуса или обновите список позже."
          icon="📋"
          title="Нет открытых блокеров для выбранного фильтра"
        />
      )}
      {items.length > 0 && (
        <TaskList items={items} onSelect={setOpenKey} selected={openKey} />
      )}
    </PageShell>
  )
}
