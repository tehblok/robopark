import { useState } from 'react'
import { api, type Report } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru, reportKindLabel, reportStatusLabel } from '../../i18n/ru'
import { Alert } from '../PageShell'
import { formatReportDate, statusBadgeClass, trackerHref } from './report-utils'

type ReportDetailProps = {
  report: Report
  parkName?: string
  canAct: boolean
  showEscalate: boolean
  onClose: () => void
  onUpdated: () => void
}

export function ReportDetail({
  report,
  parkName,
  canAct,
  showEscalate,
  onClose,
  onUpdated,
}: ReportDetailProps) {
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)

  const trackerLink = trackerHref(report.tracker_key, report.tracker_url)
  const actionsEnabled = canAct && report.status === 'open'

  async function runAction(action: () => Promise<unknown>) {
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await action()
      setSuccess('Действие выполнено.')
      onUpdated()
    } catch (actionError) {
      setError(mapApiError(actionError, ru.errors.generic))
    } finally {
      setBusy(false)
    }
  }

  function handleReturn() {
    const comment = window.prompt('Комментарий для возврата механику:')
    if (comment == null) return
    if (!comment.trim()) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => api.reportReturn(report.id, comment.trim()))
  }

  function handleDone() {
    void runAction(() => api.reportDone(report.id))
  }

  function handleEscalate() {
    const comment = window.prompt('Комментарий для эскалации администратору:')
    if (comment == null) return
    if (!comment.trim()) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => api.reportEscalate(report.id, comment.trim()))
  }

  return (
    <div className="inbox-item">
      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="card-title">{report.title}</div>
      {report.body && <p>{report.body}</p>}

      <div className="card-meta">
        <span className={statusBadgeClass(report.status)}>
          {reportStatusLabel(report.status)}
        </span>
        <span>{reportKindLabel(report.kind)}</span>
        {parkName && <span>{parkName}</span>}
        <span>#{report.id}</span>
        <span>{formatReportDate(report.created_at)}</span>
      </div>

      {trackerLink && (
        <p>
          <a href={trackerLink} rel="noreferrer" target="_blank">
            {report.tracker_key ?? 'Тикет в Tracker'}
          </a>
        </p>
      )}

      {report.return_comment && (
        <div className="detail-block">
          <strong>Комментарий при возврате</strong>
          <p>{report.return_comment}</p>
        </div>
      )}

      <div className="action-row">
        <button className="btn btn-secondary" disabled={busy} onClick={onClose} type="button">
          {ru.reports.actions.close}
        </button>
        {actionsEnabled && (
          <>
            <button className="btn btn-secondary" disabled={busy} onClick={handleReturn} type="button">
              {ru.reports.actions.return}
            </button>
            <button disabled={busy} onClick={handleDone} type="button">
              {ru.reports.actions.done}
            </button>
            {showEscalate && (
              <button className="btn btn-ghost" disabled={busy} onClick={handleEscalate} type="button">
                {ru.reports.actions.escalate}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
