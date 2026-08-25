import type { Report } from '../../api'
import { EmptyBlock, SkeletonList } from '../ui/Feedback'
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
  emptyHint?: string
  onSelect?: (report: Report) => void
  selectedId?: number | null
  showReturnComment?: boolean
}

function kindTone(kind: string): string {
  switch (kind) {
    case 'ticket_close_review':
      return 'tone-high'
    case 'mechanic_problem':
      return 'tone-blocker'
    case 'escalation_to_admin':
    case 'emergency_cookie_stale':
      return 'tone-blocker'
    case 'ticket_question':
      return 'tone-normal'
    default:
      return 'tone-low'
  }
}

export function ReportList({
  reports,
  loading,
  emptyMessage = 'Пока ничего нет',
  emptyHint,
  onSelect,
  selectedId = null,
  showReturnComment = true,
}: ReportListProps) {
  if (loading) {
    return <SkeletonList rows={4} />
  }

  if (reports.length === 0) {
    return (
      <EmptyBlock
        hint={emptyHint}
        icon="📋"
        title={emptyMessage}
      />
    )
  }

  return (
    <ul className="report-list">
      {reports.map((report) => {
        const interactive = onSelect != null
        const selected = selectedId === report.id
        const body = (
          <>
            <span className={`issue-priority-bar ${kindTone(report.kind)}`} aria-hidden="true" />
            <div className="issue-row-body">
              <div className="issue-row-top">
                <strong className="report-row-title">{report.title}</strong>
                <span className={statusBadgeClass(report.status)}>
                  {reportStatusText(report.status)}
                </span>
              </div>
              <div className="issue-row-meta">
                <span>{reportKindText(report.kind)}</span>
                {report.tracker_key && <span>{report.tracker_key}</span>}
                <span>{formatReportDate(report.created_at)}</span>
              </div>
              {showReturnComment && report.return_comment && (
                <p className="report-row-return">{report.return_comment}</p>
              )}
            </div>
          </>
        )

        if (!interactive) {
          return (
            <li className="issue-row report-row" key={report.id}>
              {body}
            </li>
          )
        }

        return (
          <li key={report.id}>
            <button
              className={`issue-row report-row${selected ? ' is-selected' : ''}`}
              onClick={() => onSelect(report)}
              type="button"
            >
              {body}
            </button>
          </li>
        )
      })}
    </ul>
  )
}
