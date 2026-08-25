import { type FormEvent, useEffect, useState } from 'react'
import { api, type TrackerTransition, type TrackerUserSuggestion } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'

export function IssueActionsPanel({
  canWrite,
  transitions,
  currentUser,
  issueUrl,
  onComment,
  onAssign,
  onUnassign,
  onTransition,
  onClose,
}: {
  canWrite: boolean
  transitions: TrackerTransition[]
  currentUser?: string
  issueUrl?: string
  onComment: (text: string) => Promise<void>
  onAssign: (assignee: string) => Promise<void>
  onUnassign: () => Promise<void>
  onTransition: (transition: string) => Promise<void>
  onClose: () => Promise<void>
}) {
  const [comment, setComment] = useState('')
  const [assignee, setAssignee] = useState('')
  const [suggestions, setSuggestions] = useState<TrackerUserSuggestion[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const query = assignee.trim()
    if (query.length < 1) {
      setSuggestions([])
      return
    }

    const timer = window.setTimeout(() => {
      void api
        .trackerUsers(query)
        .then(setSuggestions)
        .catch(() => setSuggestions([]))
    }, 250)

    return () => window.clearTimeout(timer)
  }, [assignee])

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(mapApiError(err) || ru.tracker.actions.failed)
    } finally {
      setBusy('')
    }
  }

  const submitComment = async (event: FormEvent) => {
    event.preventDefault()
    const text = comment.trim()
    if (!text) return
    await run('comment', async () => {
      await onComment(text)
      setComment('')
    })
  }

  const submitAssign = async (event: FormEvent) => {
    event.preventDefault()
    const login = assignee.trim()
    if (!login) return
    await run('assign', async () => {
      await onAssign(login)
      setAssignee('')
    })
  }

  if (!canWrite) {
    return (
      <section className="issue-actions">
        <p className="issue-muted">{ru.tracker.actionsDisabled}</p>
        {issueUrl && (
          <a className="btn btn-secondary" href={issueUrl} rel="noreferrer" target="_blank">
            {ru.tracker.actions.openInTracker}
          </a>
        )}
      </section>
    )
  }

  return (
    <section className="issue-actions">
      {error && <p className="alert alert-error">{error}</p>}

      <form className="issue-comment-form" onSubmit={submitComment}>
        <textarea
          aria-label={ru.tracker.comments}
          onChange={(event) => setComment(event.target.value)}
          placeholder={ru.tracker.commentPlaceholder}
          rows={3}
          value={comment}
        />
        <div className="issue-comment-actions">
          <span className="issue-muted">{ru.tracker.commentHint}</span>
          <button className="btn" disabled={!comment.trim() || busy === 'comment'} type="submit">
            {busy === 'comment' ? ru.loading : ru.tracker.commentSubmit}
          </button>
        </div>
      </form>

      {transitions.length > 0 && (
        <div className="issue-action-group">
          <span className="issue-action-label">{ru.tracker.actions.transitions}</span>
          <div className="issue-action-row">
            {transitions.map((item) => (
              <button
                className="btn btn-secondary"
                disabled={Boolean(busy)}
                key={item.id}
                onClick={() => void run(`t-${item.id}`, () => onTransition(item.id))}
                type="button"
              >
                {busy === `t-${item.id}` ? ru.loading : item.display}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="issue-action-group">
        <span className="issue-action-label">{ru.tracker.fields.assignee}</span>
        <div className="issue-action-row">
          <form className="issue-assign-form" onSubmit={submitAssign}>
            <input
              aria-label={ru.tracker.actions.assignPlaceholder}
              list="assignee-suggestions"
              onChange={(event) => setAssignee(event.target.value)}
              placeholder={ru.tracker.actions.assignPlaceholder}
              value={assignee}
            />
            <datalist id="assignee-suggestions">
              {suggestions.map((item) => (
                <option key={item.login} label={item.display} value={item.login} />
              ))}
            </datalist>
            <button className="btn btn-secondary" disabled={!assignee.trim() || Boolean(busy)} type="submit">
              {ru.tracker.actions.assign}
            </button>
          </form>
          {currentUser && (
            <button
              className="btn btn-secondary"
              disabled={Boolean(busy)}
              onClick={() => void run('self', () => onAssign(currentUser))}
              type="button"
            >
              {busy === 'self' ? ru.loading : ru.tracker.actions.assignSelf}
            </button>
          )}
          <button
            className="btn btn-ghost"
            disabled={Boolean(busy)}
            onClick={() => void run('unassign', onUnassign)}
            type="button"
          >
            {busy === 'unassign' ? ru.loading : ru.tracker.actions.unassign}
          </button>
        </div>
      </div>

      <div className="issue-action-footer">
        {issueUrl && (
          <a className="btn btn-ghost" href={issueUrl} rel="noreferrer" target="_blank">
            {ru.tracker.actions.openInTracker}
          </a>
        )}
        <button
          className="btn btn-danger"
          disabled={Boolean(busy)}
          onClick={() => {
            if (!window.confirm(ru.tracker.actions.confirmClose)) return
            void run('close', onClose)
          }}
          type="button"
        >
          {busy === 'close' ? ru.loading : ru.tracker.actions.close}
        </button>
      </div>
    </section>
  )
}
