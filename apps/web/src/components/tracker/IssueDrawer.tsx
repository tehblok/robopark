import { useEffect, useLayoutEffect, useRef } from 'react'
import { api, type TrackerTransition } from '../../api'
import { useAuth } from '../../auth-context'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'

/**
 * Full issue card for the tasks page: opening a task no longer means leaving
 * for a separate workspace — the ticket is worked on in place, Tracker-style.
 */
export function IssueDrawer({
  issueKey,
  canWrite,
  onClose,
  onChanged,
}: {
  issueKey: string
  canWrite: boolean
  onClose: () => void
  onChanged?: () => void
}) {
  const { user } = useAuth()

  const detailRes = useCachedResource(
    `tracker:issue:${issueKey}`,
    () => api.trackerIssue(issueKey),
  )
  const commentsRes = useCachedResource(
    `tracker:comments:${issueKey}`,
    () => api.trackerComments(issueKey),
  )
  const transitionsRes = useCachedResource<TrackerTransition[]>(
    canWrite ? `tracker:transitions:${issueKey}` : '',
    () => api.trackerTransitions(issueKey),
    { enabled: canWrite },
  )

  const detail = detailRes.data ?? null
  const comments = commentsRes.data ?? []
  const transitions = transitionsRes.data ?? []
  const loadError =
    detailRes.error ?? commentsRes.error ?? (canWrite ? transitionsRes.error : null)
  const errorText = loadError ? mapApiError(loadError) || ru.tracker.detailsError : ''
  const loading = detailRes.isLoading && !detail

  const identity = `${user?.id}:${issueKey}`
  const owner = useRef(identity)
  useLayoutEffect(() => {
    owner.current = identity
    return () => { owner.current = '' }
  }, [identity])
  const reload = async () => {
    if (owner.current !== identity) return
    resourceStore.invalidate(`tracker:issue:${issueKey}`)
    resourceStore.invalidate(`tracker:comments:${issueKey}`)
    resourceStore.invalidate(`tracker:transitions:${issueKey}`)
    await Promise.all([
      detailRes.refresh(),
      commentsRes.refresh(),
      canWrite ? transitionsRes.refresh() : Promise.resolve(),
    ])
    if (owner.current === identity) onChanged?.()
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="issue-drawer">
      <div className="issue-drawer-head">
        <button className="page-back" onClick={onClose} type="button">
          ← {ru.back}
        </button>
      </div>

      {errorText && <p className="alert alert-error">{errorText}</p>}

      <IssueDetailPanel
        comments={comments}
        commentsAsHistory={user?.role === 'mechanic'}
        issue={detail}
        loading={loading}
      />

      {detail && (
        <IssueActionsPanel
          canWrite={canWrite}
          currentUser={user?.username}
          issueUrl={detail.url}
          onAssign={async (assignee) => {
            await api.trackerAssign(issueKey, assignee)
            await reload()
          }}
          onClose={async () => {
            await api.trackerClose(issueKey)
            await reload()
          }}
          onComment={async (text) => {
            await api.trackerComment(issueKey, text)
            await reload()
          }}
          onAttach={async (file) => {
            await api.trackerAttach(issueKey, file)
            await reload()
          }}
          onTransition={async (transition) => {
            await api.trackerTransition(issueKey, transition)
            await reload()
          }}
          onUnassign={async () => {
            await api.trackerUnassign(issueKey)
            await reload()
          }}
          transitions={transitions}
        />
      )}
    </div>
  )
}
