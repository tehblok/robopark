import { useEffect, useRef, useState } from 'react'
import {
  api,
  type TrackerComment,
  type TrackerIssue,
  type TrackerIssueDetail,
  type TrackerTransition,
} from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'
import { IssueFilters, type IssueFilterValues } from './IssueFilters'
import { IssueList } from './IssueList'

export function TrackerWorkspace({
  allowUntagged,
  canWrite,
  defaultQueue = 'SDCFLEETOPS',
  defaultPark,
}: {
  allowUntagged: boolean
  canWrite: boolean
  defaultQueue?: string
  defaultPark?: string
}) {
  const [items, setItems] = useState<TrackerIssue[]>([])
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<TrackerIssueDetail | null>(null)
  const [comments, setComments] = useState<TrackerComment[]>([])
  const [transitions, setTransitions] = useState<TrackerTransition[]>([])
  const [error, setError] = useState('')
  const requestId = useRef(0)
  const didInitialLoad = useRef(false)

  const clearDetail = () => {
    setSelected('')
    setDetail(null)
    setComments([])
    setTransitions([])
  }

  const loadIssues = async (filters: IssueFilterValues) => {
    const req = ++requestId.current
    setError('')
    try {
      if (filters.untagged && !filters.queue) {
        setError('Для неразмеченных укажите очередь (например SDCFLEETOPS).')
        return
      }
      const data = await api.trackerIssues(filters)
      if (req !== requestId.current) return
      setItems(data.items)
      const mobileShell =
        typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches
      if (data.items[0] && !mobileShell) {
        await openIssue(data.items[0].key)
      } else {
        clearDetail()
      }
    } catch (err) {
      if (req !== requestId.current) return
      setError(mapApiError(err) || 'Не удалось загрузить тикеты Startrek.')
    }
  }

  useEffect(() => {
    if (didInitialLoad.current) return
    didInitialLoad.current = true
    void loadIssues({
      queue: defaultQueue,
      park: defaultPark || undefined,
    })
    // Initial load with workspace defaults only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    } catch (err) {
      setError(mapApiError(err) || 'Не удалось загрузить тикет.')
    }
  }

  const refreshSelected = async () => {
    if (selected) {
      await openIssue(selected)
    }
  }

  return (
    <section
      className={
        selected ? 'tracker-grid tracker-grid--has-detail' : 'tracker-grid'
      }
    >
      {error ? <p className="error">{error}</p> : null}
      <div className="tracker-list-pane">
        <IssueFilters
          allowUntagged={allowUntagged}
          defaultPark={defaultPark}
          defaultQueue={defaultQueue}
          onApply={(filters) => void loadIssues(filters)}
        />
        <IssueList items={items} onSelect={(key) => void openIssue(key)} selected={selected} />
      </div>
      <div className="tracker-detail-pane">
        {selected ? (
          <button
            type="button"
            className="page-back tracker-detail-back"
            onClick={clearDetail}
          >
            {ru.back}
          </button>
        ) : null}
        <IssueDetailPanel comments={comments} issue={detail} />
        {detail && (
          <IssueActionsPanel
            canWrite={canWrite}
            onAssign={async (assignee) => {
              await api.trackerAssign(detail.key, assignee)
              await refreshSelected()
            }}
            onClose={async () => {
              await api.trackerClose(detail.key)
              await refreshSelected()
            }}
            onComment={async (text) => {
              await api.trackerComment(detail.key, text)
              await refreshSelected()
            }}
            onTransition={async (transition) => {
              await api.trackerTransition(detail.key, transition)
              await refreshSelected()
            }}
            onUnassign={async () => {
              await api.trackerUnassign(detail.key)
              await refreshSelected()
            }}
            transitions={transitions}
          />
        )}
      </div>
    </section>
  )
}
