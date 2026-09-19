import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  type TrackerIssueCapabilities,
  type TrackerTransition,
  type TrackerUserSuggestion,
} from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from '../../design-system/layout/ResponsiveDisclosure'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { safeHttpUrl } from '../../lib/safeUrl'
import { pendingTrackerSubmissions, trackerReliabilityError } from './trackerReliability'
import { useCommentDraft } from './useCommentDraft'
import './task-card.css'

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
  const [isPhone, setIsPhone] = useState(() => window.matchMedia('(max-width: 899px)').matches)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const media = window.matchMedia('(max-width: 899px)')
    const change = (event: MediaQueryListEvent) => setIsPhone(event.matches)
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])

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
    if (cameraInputRef.current) cameraInputRef.current.value = ''
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
        aria-label="Выбрать фото"
        className="issue-attach-input"
        disabled={Boolean(busy)}
        onChange={handlePhotoPick}
        ref={fileInputRef}
        type="file"
      />
      {isPhone ? <input accept="image/*" aria-label="Сделать фото" capture="environment"
        className="issue-attach-input" disabled={Boolean(busy)} onChange={handlePhotoPick}
        ref={cameraInputRef} type="file" /> : null}
      <div className="issue-action-row">
        <Button
          disabled={Boolean(busy)}
          onClick={() => fileInputRef.current?.click()}
          type="button"
          variant="secondary"
        >
          {ru.tracker.attachPhotoPick}
        </Button>
        {isPhone ? <Button disabled={Boolean(busy)} onClick={() => cameraInputRef.current?.click()} type="button" variant="secondary">Сделать фото</Button> : null}
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

function IssueActionsPanelContent({
  canWrite,
  capabilities,
  transitions,
  currentUser,
  draftOwner,
  issueKey,
  issueUrl: issueUrlRaw,
  onComment,
  onAttach,
  onAssign,
  onUnassign,
  onTransition,
  onClose,
  role,
  reviewState,
  onSubmitReview,
  onReturnReview,
  onApproveReview,
}: {
  canWrite?: boolean
  capabilities?: TrackerIssueCapabilities
  transitions: TrackerTransition[]
  currentUser?: string
  draftOwner?: string
  issueKey?: string
  issueUrl?: string
  onComment: (text: string) => Promise<void>
  onAttach?: (file: File) => Promise<void>
  onAssign: (assignee: string) => Promise<void>
  onUnassign: () => Promise<void>
  onTransition: (transition: string) => Promise<void>
  onClose: () => Promise<void>
  role?: string
  reviewState?: 'pending' | 'returned' | 'closed' | null
  onSubmitReview?: () => Promise<void>
  onReturnReview?: () => Promise<void>
  onApproveReview?: () => Promise<void>
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
  const { comment, setComment, capture } = useCommentDraft(draftOwner ?? currentUser, issueKey)
  const [assignee, setAssignee] = useState('')
  const [suggestions, setSuggestions] = useState<TrackerUserSuggestion[]>([])
  const [busy, setBusy] = useState('')
  const submitting = useRef(false)
  const [pendingCount, setPendingCount] = useState(() => pendingTrackerSubmissions(draftOwner ?? currentUser, issueKey))
  useEffect(() => {
    const update = () => setPendingCount(pendingTrackerSubmissions(draftOwner ?? currentUser, issueKey))
    window.addEventListener('tracker-submissions-changed', update)
    window.addEventListener('storage', update)
    return () => { window.removeEventListener('tracker-submissions-changed', update); window.removeEventListener('storage', update) }
  }, [draftOwner, currentUser, issueKey])
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
    if (submitting.current) return false
    submitting.current = true
    setBusy(name)
    setError('')
    setSuccess('')
    if (name === 'close') setCloseError(null)
    try {
      await action()
      // Review/return only open a form; no mutation has been submitted yet.
      if (name !== 'review' && name !== 'return') setSuccess('Действие выполнено')
      return true
    } catch (caught) {
      const safeMessage = trackerReliabilityError(caught) || mapApiError(caught) || ru.tracker.actions.failed
      const message = caught instanceof ApiError && caught.requestId
        ? `${safeMessage} Код запроса: ${caught.requestId}`
        : safeMessage
      if (name === 'close') {
        setCloseError(message)
      } else {
        setError(message)
      }
      return false
    } finally {
      submitting.current = false
      setBusy('')
    }
  }

  const submitComment = async (event: FormEvent) => {
    event.preventDefault()
    const text = comment.trim()
    if (!text) return
    const clearSubmittedDraft = capture()
    if (await run('comment', () => onComment(text))) clearSubmittedDraft()
  }

  const submitAssign = async (event: FormEvent) => {
    event.preventDefault()
    const login = assignee.trim()
    if (!login) return
    if (await run('assign', () => onAssign(login))) setAssignee('')
  }

  const hasLifecycleAction = role === 'mechanic' && reviewState !== 'pending' && Boolean(onSubmitReview)
    || role === 'operator' && reviewState === 'pending' && Boolean(onReturnReview || onApproveReview)
  const hasActions = Object.values(effectiveCapabilities).some(Boolean) || hasLifecycleAction
  if (!hasActions) {
    return (
      <section className="issue-actions">
        <p className="issue-muted">{ru.tracker.actionsDisabled}</p>
        {issueUrl && !role && (
          <a className="btn btn-secondary" href={issueUrl} rel="noreferrer" target="_blank">
            {ru.tracker.actions.openInTracker}
          </a>
        )}
      </section>
    )
  }

  return (
    <section className="issue-actions">
      {pendingCount > 0 && !busy && <p role="status">Есть отправка без подтверждения. Проверьте историю Tracker перед изменением текста или новой отправкой. Повтор того же содержимого использует сохранённый ключ.</p>}
      {error && <p className="alert alert-error" role="alert">{error}</p>}
      {success && <p aria-live="polite">{success}</p>}

      <div className="issue-action-primary" id="comment">
        {effectiveCapabilities.attach && onAttach && (
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
      </div>

      {role === 'mechanic' && reviewState !== 'pending' && onSubmitReview ? (
        <Button disabled={Boolean(busy)} onClick={() => void run('review', onSubmitReview)} type="button">
          Передать на проверку
        </Button>
      ) : null}
      {role === 'operator' && reviewState === 'pending' ? <div className="issue-action-row">
        {onReturnReview ? <Button disabled={Boolean(busy)} onClick={() => void run('return', onReturnReview)} type="button" variant="secondary">Вернуть в работу</Button> : null}
        {onApproveReview ? <Button disabled={Boolean(busy)} onClick={() => void run('approve', onApproveReview)} type="button">Принять и закрыть</Button> : null}
      </div> : null}

      {!role ? <ResponsiveDisclosureGroup label="Дополнительные действия задачи">
        {(effectiveCapabilities.transition && transitions.length > 0) || issueUrl || effectiveCapabilities.close ? (
          <ResponsiveDisclosure id="transition" title="Статус задачи">
            <div id="transition">
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
            </div>
          </ResponsiveDisclosure>
        ) : null}

        {(effectiveCapabilities.assign || effectiveCapabilities.unassign) && (
          <ResponsiveDisclosure id="assignee" title={ru.tracker.fields.assignee}>
            <div className="issue-action-group" id="assignee">
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
          </ResponsiveDisclosure>
        )}
      </ResponsiveDisclosureGroup> : null}

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

export function IssueActionsPanel(props: React.ComponentProps<typeof IssueActionsPanelContent>) {
  return <IssueActionsPanelContent {...props} key={JSON.stringify([props.draftOwner ?? props.currentUser, props.issueKey])} />
}
