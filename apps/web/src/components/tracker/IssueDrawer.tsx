import { useEffect, useState } from 'react'
import {
  api,
  type TrackerComment,
  type TrackerIssueDetail,
  type TrackerTransition,
} from '../../api'
import { useAuth } from '../../auth-context'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
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
  const [detail, setDetail] = useState<TrackerIssueDetail | null>(null)
  const [comments, setComments] = useState<TrackerComment[]>([])
  const [transitions, setTransitions] = useState<TrackerTransition[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    Promise.all([
      api.trackerIssue(issueKey),
      api.trackerComments(issueKey),
      canWrite ? api.trackerTransitions(issueKey) : Promise.resolve([]),
    ])
      .then(([issue, issueComments, issueTransitions]) => {
        if (cancelled) return
        setDetail(issue)
        setComments(issueComments)
        setTransitions(issueTransitions)
      })
      .catch((err) => {
        if (cancelled) return
        setError(mapApiError(err) || ru.tracker.detailsError)
        setDetail(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [issueKey, canWrite])

  const reload = async () => {
    const [issue, issueComments, issueTransitions] = await Promise.all([
      api.trackerIssue(issueKey),
      api.trackerComments(issueKey),
      canWrite ? api.trackerTransitions(issueKey) : Promise.resolve([]),
    ])
    setDetail(issue)
    setComments(issueComments)
    setTransitions(issueTransitions)
    onChanged?.()
  }

  // Escape closes the card, matching the usual drawer behaviour.
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

      {error && <p className="alert alert-error">{error}</p>}

      <IssueDetailPanel comments={comments} issue={detail} loading={loading} />

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
