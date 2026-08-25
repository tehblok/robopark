import { useState } from 'react'
import { api, type Report } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru, reportKindLabel, reportStatusLabel } from '../../i18n/ru'
import { Alert } from '../PageShell'
import { Spinner } from '../ui/Feedback'
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
  const [returnComment, setReturnComment] = useState('')
  const [escalateComment, setEscalateComment] = useState('')
  const [actionMode, setActionMode] = useState<'idle' | 'return' | 'escalate'>('idle')

  const trackerLink = trackerHref(report.tracker_key, report.tracker_url)
  const actionsEnabled = canAct && report.status === 'open'

  async function runAction(action: () => Promise<unknown>) {
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await action()
      setSuccess('Действие выполнено.')
      setActionMode('idle')
      setReturnComment('')
      setEscalateComment('')
      onUpdated()
    } catch (actionError) {
      setError(mapApiError(actionError, ru.errors.generic))
    } finally {
      setBusy(false)
    }
  }

  function handleReturn() {
    const comment = returnComment.trim()
    if (!comment) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => api.reportReturn(report.id, comment))
  }

  function handleEscalate() {
    const comment = escalateComment.trim()
    if (!comment) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => api.reportEscalate(report.id, comment))
  }

  return (
    <article className="report-detail">
      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <header className="report-detail-head">
        <div className="report-detail-title-row">
          <h3 className="report-detail-title">{report.title}</h3>
          <span className={statusBadgeClass(report.status)}>
            {reportStatusLabel(report.status)}
          </span>
          <span className="badge badge-muted">{reportKindLabel(report.kind)}</span>
        </div>
      </header>

      <dl className="report-detail-fields">
        <div className="issue-field">
          <dt>Парк</dt>
          <dd>{parkName ?? (report.park_id == null ? 'Платформа' : `#${report.park_id}`)}</dd>
        </div>
        <div className="issue-field">
          <dt>Автор</dt>
          <dd>#{report.author_user_id}</dd>
        </div>
        <div className="issue-field">
          <dt>Создан</dt>
          <dd>{formatReportDate(report.created_at)}</dd>
        </div>
        {trackerLink && (
          <div className="issue-field">
            <dt>Тикет</dt>
            <dd>
              <a href={trackerLink} rel="noreferrer" target="_blank">
                {report.tracker_key ?? 'Открыть в Tracker'}
              </a>
            </dd>
          </div>
        )}
      </dl>

      {report.body && (
        <div className="report-detail-body">
          <strong>Описание</strong>
          <p>{report.body}</p>
        </div>
      )}

      {report.return_comment && (
        <Alert tone="warning">
          Комментарий при возврате: {report.return_comment}
        </Alert>
      )}

      {actionMode === 'return' && (
        <div className="issue-comment-form">
          <label className="field">
            <span className="field-label">Комментарий для возврата *</span>
            <textarea
              onChange={(event) => setReturnComment(event.target.value)}
              placeholder="Почему возвращаете механику"
              rows={3}
              value={returnComment}
            />
          </label>
          <div className="form-actions">
            <button
              className="btn"
              disabled={busy || !returnComment.trim()}
              onClick={handleReturn}
              type="button"
            >
              {busy ? <Spinner label={ru.reports.actions.return} /> : ru.reports.actions.return}
            </button>
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => setActionMode('idle')}
              type="button"
            >
              {ru.reports.actions.cancel}
            </button>
          </div>
        </div>
      )}

      {actionMode === 'escalate' && (
        <div className="issue-comment-form">
          <label className="field">
            <span className="field-label">Комментарий для эскалации *</span>
            <textarea
              onChange={(event) => setEscalateComment(event.target.value)}
              placeholder="Почему эскалируете админу"
              rows={3}
              value={escalateComment}
            />
          </label>
          <div className="form-actions">
            <button
              className="btn"
              disabled={busy || !escalateComment.trim()}
              onClick={handleEscalate}
              type="button"
            >
              {busy ? <Spinner label={ru.reports.actions.escalate} /> : ru.reports.actions.escalate}
            </button>
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => setActionMode('idle')}
              type="button"
            >
              {ru.reports.actions.cancel}
            </button>
          </div>
        </div>
      )}

      {actionMode === 'idle' && (
        <div className="form-actions report-detail-actions">
          <button className="btn btn-secondary" disabled={busy} onClick={onClose} type="button">
            {ru.reports.actions.close}
          </button>
          {actionsEnabled && (
            <>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => {
                  setError('')
                  setActionMode('return')
                }}
                type="button"
              >
                {ru.reports.actions.return}
              </button>
              <button
                className="btn"
                disabled={busy}
                onClick={() => void runAction(() => api.reportDone(report.id))}
                type="button"
              >
                {busy ? <Spinner label={ru.reports.actions.done} /> : ru.reports.actions.done}
              </button>
              {showEscalate && (
                <button
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => {
                    setError('')
                    setActionMode('escalate')
                  }}
                  type="button"
                >
                  {ru.reports.actions.escalate}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </article>
  )
}
