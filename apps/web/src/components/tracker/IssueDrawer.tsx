import { TaskCollaboration } from './TaskCollaboration'
import { attachmentIdentity, runTrackerSubmission } from './trackerReliability'
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

        {detail && <TaskCollaboration issueKey={detail.key} owner={user?.username ?? ''} active canWrite={canWrite} />}

      <IssueDetailPanel
        comments={comments}
        commentsAsHistory={user?.role === 'mechanic'}
        issue={detail}
        loading={loading}
      />

      {detail && (
        <IssueActionsPanel
          canWrite={canWrite}
          draftOwner={user?.username}
          issueKey={issueKey}
          currentUser={user?.username}
          issueUrl={detail.url}
          onAssign={async (assignee) => {
            await runTrackerSubmission(user?.username ?? '', detail, 'assign', { assignee }, headers => api.trackerAssign(issueKey, assignee, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          onClose={async () => {
            await runTrackerSubmission(user?.username ?? '', detail, 'close', {}, headers => api.trackerClose(issueKey, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          onComment={async (text) => {
            await runTrackerSubmission(user?.username ?? '', detail, 'comment', { text }, headers => api.trackerComment(issueKey, text, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          onAttach={async (file) => {
            await runTrackerSubmission(user?.username ?? '', detail, 'attach', await attachmentIdentity(file), headers => api.trackerAttach(issueKey, file, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          onTransition={async (transition) => {
            await runTrackerSubmission(user?.username ?? '', detail, 'transition', { transition }, headers => api.trackerTransition(issueKey, transition, undefined, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          onUnassign={async () => {
            await runTrackerSubmission(user?.username ?? '', detail, 'unassign', {}, headers => api.trackerUnassign(issueKey, headers), () => { if (owner.current !== identity) throw new Error('work_access_changed') })
            await reload()
          }}
          transitions={transitions}
        />
      )}
    </div>
  )
}
