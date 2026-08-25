import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type TrackerComment,
  type TrackerIssue,
  type TrackerIssueDetail,
  type TrackerTransition,
} from '../../api'
import { useAuth } from '../../auth-context'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'
import { IssueFilters, type IssueFilterValues } from './IssueFilters'
import { IssueList } from './IssueList'

const PAGE_SIZE = 50

function isMobileShell() {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches
}

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
  const { user } = useAuth()
  const [items, setItems] = useState<TrackerIssue[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<TrackerIssueDetail | null>(null)
  const [comments, setComments] = useState<TrackerComment[]>([])
  const [transitions, setTransitions] = useState<TrackerTransition[]>([])
  const [error, setError] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  const requestId = useRef(0)
  const didInitialLoad = useRef(false)
  const lastFilters = useRef<IssueFilterValues>({})

  const clearDetail = () => {
    setSelected('')
    setDetail(null)
    setComments([])
    setTransitions([])
  }

  const openIssue = useCallback(async (key: string) => {
    setSelected(key)
    setError('')
    setDetailLoading(true)
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
      setError(mapApiError(err) || ru.tracker.detailsError)
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const loadIssues = useCallback(
    async (filters: IssueFilterValues) => {
      const req = ++requestId.current
      setError('')
      setListLoading(true)
      lastFilters.current = filters
      try {
        if (filters.untagged && !filters.queue) {
          setError(ru.errors.details.tracker_queue_required_for_untagged)
          return
        }
        const data = await api.trackerIssues({ ...filters, limit: PAGE_SIZE, offset: 0 })
        if (req !== requestId.current) return
        setItems(data.items)
        setTotal(data.total)
        setHasMore(data.has_more)

        // On desktop preselect the first issue; on mobile the list is the
        // primary view and the detail pane opens on tap.
        if (data.items[0] && !isMobileShell()) {
          await openIssue(data.items[0].key)
        } else {
          clearDetail()
        }
      } catch (err) {
        if (req !== requestId.current) return
        setError(mapApiError(err) || ru.tracker.loadError)
      } finally {
        if (req === requestId.current) setListLoading(false)
      }
    },
    [openIssue],
  )

  const loadMore = async () => {
    setListLoading(true)
    try {
      const data = await api.trackerIssues({
        ...lastFilters.current,
        limit: PAGE_SIZE,
        offset: items.length,
      })
      setItems((prev) => [...prev, ...data.items])
      setTotal(data.total)
      setHasMore(data.has_more)
    } catch (err) {
      setError(mapApiError(err) || ru.tracker.loadError)
    } finally {
      setListLoading(false)
    }
  }

  useEffect(() => {
    if (didInitialLoad.current) return
    didInitialLoad.current = true
    void loadIssues({ queue: defaultQueue, park: defaultPark || undefined })
    // Initial load uses workspace defaults only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshSelected = async () => {
    if (selected) await openIssue(selected)
  }

  // After a write the list may be stale (status changed, ticket closed).
  const refreshAll = async () => {
    await refreshSelected()
    const data = await api.trackerIssues({
      ...lastFilters.current,
      limit: Math.max(PAGE_SIZE, items.length),
      offset: 0,
    })
    setItems(data.items)
    setTotal(data.total)
    setHasMore(data.has_more)
  }

  return (
    <section className={`tracker-grid${selected ? ' tracker-grid--has-detail' : ''}`}>
      {error && <p className="alert alert-error tracker-error">{error}</p>}

      <div className="tracker-list-pane">
        <IssueFilters
          allowUntagged={allowUntagged}
          defaultPark={defaultPark}
          defaultQueue={defaultQueue}
          loading={listLoading}
          onApply={(filters) => void loadIssues(filters)}
          trackerLogin={user?.tracker_login}
        />
        <IssueList
          hasMore={hasMore}
          items={items}
          loading={listLoading}
          onLoadMore={() => void loadMore()}
          onSelect={(key) => void openIssue(key)}
          selected={selected}
          total={total}
        />
      </div>

      <div className="tracker-detail-pane">
        {selected && (
          <button className="page-back tracker-detail-back" onClick={clearDetail} type="button">
            ← {ru.back}
          </button>
        )}

        <IssueDetailPanel comments={comments} issue={detail} loading={detailLoading} />

        {detail && (
          <IssueActionsPanel
            canWrite={canWrite}
            currentUser={user?.tracker_login ?? undefined}
            issueUrl={detail.url}
            onAssign={async (assignee) => {
              await api.trackerAssign(detail.key, assignee)
              await refreshAll()
            }}
            onClose={async () => {
              await api.trackerClose(detail.key)
              await refreshAll()
            }}
            onComment={async (text) => {
              await api.trackerComment(detail.key, text)
              await refreshSelected()
            }}
            onTransition={async (transition) => {
              await api.trackerTransition(detail.key, transition)
              await refreshAll()
            }}
            onUnassign={async () => {
              await api.trackerUnassign(detail.key)
              await refreshAll()
            }}
            transitions={transitions}
          />
        )}
      </div>
    </section>
  )
}
