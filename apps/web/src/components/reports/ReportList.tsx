import type { Report } from '../../api'
import { ru } from '../../i18n/ru'
import { EmptyState } from '../PageShell'
import {
  formatReportDate,
  reportKindText,
  reportStatusText,
  statusBadgeClass,
} from './report-utils'

type ReportListProps = {
  reports: Report[]
  loading: boolean
  emptyMessage?: string
  onSelect?: (report: Report) => void
  showReturnComment?: boolean
}

export function ReportList({
  reports,
  loading,
  emptyMessage = ru.empty,
  onSelect,
  showReturnComment = true,
}: ReportListProps) {
  if (loading) {
    return <EmptyState>{ru.loading}</EmptyState>
  }

  if (reports.length === 0) {
    return <EmptyState>{emptyMessage}</EmptyState>
  }

  return (
    <ul className="card-list">
      {reports.map((report) => {
        const interactive = onSelect != null
        const content = (
          <>
            <div className="card-title">{report.title}</div>
            {report.body && <p>{report.body}</p>}
            <div className="card-meta">
              <span className={statusBadgeClass(report.status)}>
                {reportStatusText(report.status)}
              </span>
              <span>{reportKindText(report.kind)}</span>
              {report.tracker_key && <span>{report.tracker_key}</span>}
              <span>{formatReportDate(report.created_at)}</span>
            </div>
            {showReturnComment && report.return_comment && (
              <div className="detail-block">
                <strong>Комментарий при возврате</strong>
                <p>{report.return_comment}</p>
              </div>
            )}
          </>
        )

        if (!interactive) {
          return (
            <li className="card" key={report.id}>
              {content}
            </li>
          )
        }

        return (
          <li key={report.id}>
            <button
              className="tracker-item"
              onClick={() => onSelect(report)}
              type="button"
            >
              <div className="report-list-body">{content}</div>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
