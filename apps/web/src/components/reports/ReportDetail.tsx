import { useLayoutEffect, useRef, useState } from 'react'
import { api, type Report } from '../../api'
import type { ReportsApiClient } from '../../domains/reports/reports'
import { mapApiError } from '../../i18n/errors'
import { ru, reportKindLabel, reportStatusLabel } from '../../i18n/ru'
import { Alert } from '../PageShell'
import { Spinner } from '../ui/Feedback'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { formatReportDate, trackerHref } from './report-utils'

type ReportDetailProps = {
  apiClient?: ReportsApiClient
  report: Report
  ownerKey: string
  parkName?: string
  canAct: boolean
  canResubmit?: boolean
  showEscalate: boolean
  onClose: () => void
  onUpdated: () => void
}

export function ReportDetail({
  apiClient = api,
  report,
  ownerKey,
  parkName,
  canAct,
  canResubmit = false,
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
  const [editTitle, setEditTitle] = useState(report.title)
  const [editBody, setEditBody] = useState(report.body)
  const [editTrackerKey, setEditTrackerKey] = useState(report.tracker_key ?? '')
  const ownerRef = useRef(ownerKey)
  const generationRef = useRef(0)
  const mountedRef = useRef(true)

  useLayoutEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      generationRef.current += 1
    }
  }, [])

  useLayoutEffect(() => {
    if (ownerRef.current === ownerKey) return
    ownerRef.current = ownerKey
    generationRef.current += 1
    setError('')
    setSuccess('')
    setBusy(false)
    setReturnComment('')
    setEscalateComment('')
    setActionMode('idle')
  }, [ownerKey])

  const trackerLink = trackerHref(report.tracker_key, report.tracker_url)
  const actionsEnabled = canAct && report.status === 'open'

  async function runAction(action: () => Promise<unknown>) {
    const generation = generationRef.current
    const requestedOwner = ownerKey
    const current = () => mountedRef.current
      && generationRef.current === generation
      && ownerRef.current === requestedOwner
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await action()
      if (!current()) return
      setSuccess('Действие выполнено.')
      setActionMode('idle')
      setReturnComment('')
      setEscalateComment('')
      onUpdated()
    } catch (actionError) {
      if (!current()) return
      setError(mapApiError(actionError, ru.errors.generic))
    } finally {
      if (current()) setBusy(false)
    }
  }

  function handleReturn() {
    const comment = returnComment.trim()
    if (!comment) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => apiClient.reportReturn(report.id, comment))
  }

  function handleEscalate() {
    const comment = escalateComment.trim()
    if (!comment) {
      setError('Комментарий обязателен.')
      return
    }
    void runAction(() => apiClient.reportEscalate(report.id, comment))
  }

  function handleResubmit() {
    const title = editTitle.trim()
    if (!title) {
      setError('Укажите заголовок.')
      return
    }
    const trackerKey = editTrackerKey.trim()
    void runAction(() => apiClient.reportResubmit(report.id, {
      title,
      body: editBody.trim(),
      tracker_key: trackerKey || null,
      tracker_url: trackerKey ? `https://st.yandex-team.ru/${trackerKey}` : null,
    }))
  }

  return (
    <article className="report-detail">
      {error && <Alert tone="error">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <header className="report-detail-head">
        <div className="report-detail-title-row">
          <h3 className="report-detail-title">{report.title}</h3>
          <StatusBadge tone={report.status === 'done' ? 'success' : report.status === 'returned' ? 'warning' : 'info'}>
            {reportStatusLabel(report.status)}
          </StatusBadge>
          <StatusBadge tone="neutral">{reportKindLabel(report.kind)}</StatusBadge>
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

      {(report.attachments?.length ?? 0) > 0 && (
        <div className="report-detail-body">
          <strong>Вложения</strong>
          <ul>
            {report.attachments?.map((attachment) => (
              <li key={attachment.id}>
                <a href={apiClient.reportAttachmentUrl(report.id, attachment.id)}>
                  {attachment.filename}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.return_comment && (
        <Alert tone="warning">
          Комментарий при возврате: {report.return_comment}
        </Alert>
      )}

      {canResubmit && report.status === 'returned' && (
        <section className="issue-comment-form" aria-label="Исправить и повторно отправить">
          <label className="field"><span className="field-label">Заголовок</span><input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label>
          <label className="field"><span className="field-label">Описание</span><textarea rows={4} value={editBody} onChange={(event) => setEditBody(event.target.value)} /></label>
          <label className="field"><span className="field-label">Тикет Tracker</span><input value={editTrackerKey} onChange={(event) => setEditTrackerKey(event.target.value)} /></label>
          <button className="btn" disabled={busy || !editTitle.trim()} onClick={handleResubmit} type="button">Повторно отправить</button>
        </section>
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
                onClick={() => void runAction(() => apiClient.reportDone(report.id))}
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
