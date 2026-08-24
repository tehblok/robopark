import { useRef, useState } from 'react'
import { api, type TrackerComment, type TrackerIssue, type TrackerIssueDetail, type TrackerTransition } from '../../api'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'
import { IssueFilters } from './IssueFilters'
import { IssueList } from './IssueList'

export function TrackerWorkspace({ allowUntagged, canWrite }: { allowUntagged: boolean; canWrite: boolean }) {
  const [items, setItems] = useState<TrackerIssue[]>([])
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<TrackerIssueDetail | null>(null)
  const [comments, setComments] = useState<TrackerComment[]>([])
  const [transitions, setTransitions] = useState<TrackerTransition[]>([])
  const [error, setError] = useState('')
  const requestId = useRef(0)

  const loadIssues = async (filters: { status?: string; robot?: string; untagged?: boolean }) => {
    const req = ++requestId.current
    setError('')
    try {
      const data = await api.trackerIssues(filters)
      if (req !== requestId.current) return
      setItems(data.items)
      if (data.items[0]) {
        await openIssue(data.items[0].key)
      }
    } catch {
      if (req !== requestId.current) return
      setError('Could not load tracker issues.')
    }
  }

  const openIssue = async (key: string) => {
    setSelected(key)
    setError('')
    try {
      const [issue, issueComments, issueTransitions] = await Promise.all([
        api.trackerIssue(key),
        api.trackerComments(key),
        api.trackerTransitions(key),
      ])
      setDetail(issue)
      setComments(issueComments)
      setTransitions(issueTransitions)
    } catch {
      setError('Could not load issue details.')
    }
  }

  const refreshSelected = async () => {
    if (selected) {
      await openIssue(selected)
    }
  }

  return (
    <section className="tracker-grid">
      <IssueFilters allowUntagged={allowUntagged} onApply={loadIssues} />
      {error && <p className="error">{error}</p>}
      <IssueList items={items} onSelect={(key) => void openIssue(key)} selected={selected} />
      <IssueDetailPanel comments={comments} issue={detail} />
      {detail && (
        <IssueActionsPanel
          canWrite={canWrite}
          onAssign={async (assignee) => { await api.trackerAssign(detail.key, assignee); await refreshSelected() }}
          onClose={async () => { await api.trackerClose(detail.key); await refreshSelected() }}
          onComment={async (text) => { await api.trackerComment(detail.key, text); await refreshSelected() }}
          onTransition={async (transition) => { await api.trackerTransition(detail.key, transition); await refreshSelected() }}
          onUnassign={async () => { await api.trackerUnassign(detail.key); await refreshSelected() }}
          transitions={transitions}
        />
      )}
    </section>
  )
}
