import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from 'react'
import {
  api,
  type TrackerIssueCapabilities,
  type TrackerTransition,
  type TrackerUserSuggestion,
} from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { safeHttpUrl } from '../../lib/safeUrl'

const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024

type RunAction = (name: string, action: () => Promise<void>) => Promise<boolean>

function AttachmentActions({
  busy,
  onAttach,
  onError,
  run,
}: {
  busy: string
  onAttach?: (file: File) => Promise<void>
  onError: (message: string) => void
  run: RunAction
}) {
  const [photoFile, setPhotoFile] = useState<File | null>(null)
  const [photoPreview, setPhotoPreview] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!photoFile) {
      setPhotoPreview('')
      return
    }
    const url = URL.createObjectURL(photoFile)
    setPhotoPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [photoFile])

  const clearPhoto = () => {
    setPhotoFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handlePhotoPick = (event: ChangeEvent<HTMLInputElement>) => {
    onError('')
    const file = event.target.files?.[0]
    if (!file) {
      clearPhoto()
      return
    }
    if (!file.type.startsWith('image/') && !/\.(jpe?g|png|webp|heic|heif)$/i.test(file.name)) {
      onError(ru.tracker.attachPhotoInvalidType)
      clearPhoto()
      return
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      onError(ru.tracker.attachPhotoTooLarge)
      clearPhoto()
      return
    }
    setPhotoFile(file)
  }

  const submitPhoto = async () => {
    if (!photoFile || !onAttach) return
    if (await run('attach', () => onAttach(photoFile))) clearPhoto()
  }

  return (
    <div className="issue-action-group issue-attach-group">
      <span className="issue-action-label">{ru.tracker.attachPhoto}</span>
      <p className="issue-muted">{ru.tracker.attachPhotoHint}</p>
      <input
        accept="image/*"
        capture="environment"
        className="issue-attach-input"
        disabled={Boolean(busy)}
        onChange={handlePhotoPick}
        ref={fileInputRef}
        type="file"
      />
      <div className="issue-action-row">
        <Button
          disabled={Boolean(busy)}
          onClick={() => fileInputRef.current?.click()}
          type="button"
          variant="secondary"
        >
          {ru.tracker.attachPhotoPick}
        </Button>
        {photoFile && (
          <Button
            busy={busy === 'attach'}
            disabled={Boolean(busy)}
            onClick={() => void submitPhoto()}
            type="button"
          >
            {busy === 'attach' ? ru.loading : ru.tracker.attachPhotoSubmit}
          </Button>
        )}
      </div>
      {photoPreview && <img alt="" className="issue-attach-preview" src={photoPreview} />}
    </div>
  )
}

export function IssueActionsPanel({
  canWrite,
  capabilities,
  transitions,
  currentUser,
  issueKey,
  issueUrl: issueUrlRaw,
  onComment,
  onAttach,
  onAssign,
  onUnassign,
  onTransition,
  onClose,
}: {
  canWrite?: boolean
  capabilities?: TrackerIssueCapabilities
  transitions: TrackerTransition[]
  currentUser?: string
  issueKey?: string
  issueUrl?: string
  onComment: (text: string) => Promise<void>
  onAttach?: (file: File) => Promise<void>
  onAssign: (assignee: string) => Promise<void>
  onUnassign: () => Promise<void>
  onTransition: (transition: string) => Promise<void>
  onClose: () => Promise<void>
}) {
  const issueUrl = safeHttpUrl(issueUrlRaw) ?? undefined
  const effectiveCapabilities: TrackerIssueCapabilities = capabilities ?? {
    comment: Boolean(canWrite),
    assign: Boolean(canWrite),
    unassign: Boolean(canWrite),
    transition: Boolean(canWrite),
    close: Boolean(canWrite),
    attach: Boolean(onAttach),
  }
  const [comment, setComment] = useState('')
  const [assignee, setAssignee] = useState('')
  const [suggestions, setSuggestions] = useState<TrackerUserSuggestion[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [closeOpen, setCloseOpen] = useState(false)
  const [closeError, setCloseError] = useState<string | null>(null)

  useEffect(() => {
    if (!effectiveCapabilities.assign) {
      setSuggestions([])
      return
    }

    const query = assignee.trim()
    if (query.length < 1) {
      setSuggestions([])
      return
    }

    let cancelled = false
    const timer = globalThis.setTimeout(() => {
      void api
        .trackerUsers(query)
        .then((next) => {
          if (!cancelled) setSuggestions(next)
        })
        .catch(() => {
          if (!cancelled) setSuggestions([])
        })
    }, 250)

    return () => {
      cancelled = true
      globalThis.clearTimeout(timer)
    }
  }, [assignee, effectiveCapabilities.assign])

  useEffect(() => {
    if (effectiveCapabilities.close) return
    setCloseOpen(false)
    setCloseError(null)
  }, [effectiveCapabilities.close])

  const run: RunAction = async (name, action) => {
    setBusy(name)
    setError('')
    setSuccess('')
    if (name === 'close') setCloseError(null)
    try {
      await action()
      setSuccess('Действие выполнено')
      return true
    } catch (caught) {
      const message = mapApiError(caught) || ru.tracker.actions.failed
      if (name === 'close') {
        setCloseError(message)
      } else {
        setError(message)
      }
      return false
    } finally {
      setBusy('')
    }
  }

  const submitComment = async (event: FormEvent) => {
    event.preventDefault()
    const text = comment.trim()
    if (!text) return
    if (await run('comment', () => onComment(text))) setComment('')
  }

  const submitAssign = async (event: FormEvent) => {
    event.preventDefault()
    const login = assignee.trim()
    if (!login) return
    if (await run('assign', () => onAssign(login))) setAssignee('')
  }

  const hasActions = Object.values(effectiveCapabilities).some(Boolean)
  if (!hasActions) {
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
      {error && <p className="alert alert-error" role="alert">{error}</p>}
      {success && <p aria-live="polite">{success}</p>}

      {effectiveCapabilities.attach && (
        <AttachmentActions
          busy={busy}
          onAttach={onAttach}
          onError={(message) => {
            setError(message)
            setSuccess('')
          }}
          run={run}
        />
      )}

      {effectiveCapabilities.comment && (
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
            <Button
              busy={busy === 'comment'}
              disabled={!comment.trim() || Boolean(busy)}
              type="submit"
            >
              {busy === 'comment' ? ru.loading : ru.tracker.commentSubmit}
            </Button>
          </div>
        </form>
      )}

      {effectiveCapabilities.transition && transitions.length > 0 && (
        <div className="issue-action-group">
          <span className="issue-action-label">{ru.tracker.actions.transitions}</span>
          <div className="issue-action-row">
            {transitions.map((item) => (
              <Button
                busy={busy === `t-${item.id}`}
                disabled={Boolean(busy)}
                key={item.id}
                onClick={() => void run(`t-${item.id}`, () => onTransition(item.id))}
                type="button"
                variant="secondary"
              >
                {busy === `t-${item.id}` ? ru.loading : item.display}
              </Button>
            ))}
          </div>
        </div>
      )}

      {(effectiveCapabilities.assign || effectiveCapabilities.unassign) && (
        <div className="issue-action-group">
          <span className="issue-action-label">{ru.tracker.fields.assignee}</span>
          <div className="issue-action-row">
            {effectiveCapabilities.assign && (
              <>
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
                  <Button
                    disabled={!assignee.trim() || Boolean(busy)}
                    type="submit"
                    variant="secondary"
                  >
                    {busy === 'assign' ? ru.loading : ru.tracker.actions.assign}
                  </Button>
                </form>
                {currentUser && (
                  <Button
                    busy={busy === 'self'}
                    disabled={Boolean(busy)}
                    onClick={() => void run('self', () => onAssign(currentUser))}
                    type="button"
                    variant="secondary"
                  >
                    {busy === 'self' ? ru.loading : ru.tracker.actions.assignSelf}
                  </Button>
                )}
              </>
            )}
            {effectiveCapabilities.unassign && (
              <Button
                busy={busy === 'unassign'}
                disabled={Boolean(busy)}
                onClick={() => void run('unassign', onUnassign)}
                type="button"
                variant="ghost"
              >
                {busy === 'unassign' ? ru.loading : ru.tracker.actions.unassign}
              </Button>
            )}
          </div>
        </div>
      )}

      {(issueUrl || effectiveCapabilities.close) && (
        <div className="issue-action-footer">
          {issueUrl && (
            <a className="btn btn-ghost" href={issueUrl} rel="noreferrer" target="_blank">
              {ru.tracker.actions.openInTracker}
            </a>
          )}
          {effectiveCapabilities.close && (
            <Button
              disabled={Boolean(busy)}
              onClick={() => {
                setError('')
                setSuccess('')
                setCloseError(null)
                setCloseOpen(true)
              }}
              type="button"
              variant="danger"
            >
              {ru.tracker.actions.close}
            </Button>
          )}
        </div>
      )}

      <ConfirmDialog
        cancelLabel="Отмена"
        confirmLabel="Подтвердить закрытие"
        description={issueKey
          ? `Задача ${issueKey} будет закрыта в Tracker и останется в истории.`
          : 'Задача будет закрыта в Tracker и останется в истории.'}
        error={closeError}
        onConfirm={async () => {
          if (await run('close', onClose)) setCloseOpen(false)
        }}
        onOpenChange={(open) => {
          setCloseOpen(open)
          if (!open) setCloseError(null)
        }}
        open={closeOpen}
        pending={busy === 'close'}
        title="Закрыть задачу?"
        tone="danger"
      />
    </section>
  )
}
