import type { Report } from '../../api'
import { EmptyBlock, SkeletonList } from '../ui/Feedback'
import { EntityRow } from '../../design-system/data/EntityRow'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import {
  formatReportDate,
  reportKindText,
  reportStatusText,
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
          <EntityRow title={report.title}
            status={<StatusBadge tone={report.status === 'done' ? 'success' : report.status === 'returned' ? 'warning' : 'info'}>{reportStatusText(report.status)}</StatusBadge>}
            meta={<>
              <span>{[reportKindText(report.kind), report.tracker_key, formatReportDate(report.created_at)].filter(Boolean).join(' · ')}</span>
              {showReturnComment && report.return_comment && (
                <p className="report-row-return">{report.return_comment}</p>
              )}
            </>} />
        )

        if (!interactive) {
          return (
            <li key={report.id}>
              {body}
            </li>
          )
        }

        return (
          <li key={report.id}>
            <button
              aria-label={`Открыть репорт ${report.title}`}
              aria-pressed={selected}
              className={`rp-report-select${selected ? ' is-selected' : ''}`}
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
