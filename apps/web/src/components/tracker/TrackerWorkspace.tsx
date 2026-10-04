import { TaskCollaboration } from './TaskCollaboration'
import { attachmentIdentity, runTrackerSubmission } from './trackerReliability'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  api,
  type TrackerIssue,
  type TrackerTransition,
} from '../../api'
import { useAuth } from '../../auth-context'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'
import { IssueFilters, type IssueFilterValues } from './IssueFilters'
import { IssueList } from './IssueList'

const PAGE_SIZE = 50

type ListPage = {
  items: TrackerIssue[]
  total: number
  has_more: boolean
}

function isMobileShell() {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches
}

function listKey(filters: IssueFilterValues): string {
  return `tracker:list:${JSON.stringify(filters)}`
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
  const [filters, setFilters] = useState<IssueFilterValues>({
    queue: defaultQueue,
    park: defaultPark || undefined,
  })
  const [selected, setSelected] = useState('')
  const [validationError, setValidationError] = useState('')
  const loadMoreRef = useRef(false)
  const loadedExtent = useRef<{ key: string; items: number } | null>(null)
  const appendRequest = useRef<{ key: string; base: ListPage } | null>(null)

  const invalidFilter = filters.untagged && !filters.queue
  const currentListKey = invalidFilter ? '' : listKey(filters)
  const listRes = useCachedResource<ListPage>(
    currentListKey,
    async () => {
      const append = appendRequest.current
      appendRequest.current = null
      if (append?.key === currentListKey) {
        const page = await api.trackerIssues({ ...filters, limit: PAGE_SIZE, offset: append.base.items.length })
        return { ...page, items: [...append.base.items, ...page.items] }
      }
      const remembered = loadedExtent.current?.key === currentListKey ? loadedExtent.current.items : 0
      const target = Math.max(PAGE_SIZE, remembered, resourceStore.get<ListPage>(currentListKey)?.items.length ?? 0)
      loadedExtent.current = { key: currentListKey, items: target }
      const refreshed: TrackerIssue[] = []
      let page: ListPage
      do {
        page = await api.trackerIssues({ ...filters, limit: PAGE_SIZE, offset: refreshed.length })
        refreshed.push(...page.items)
      } while (refreshed.length < target && page.has_more && page.items.length > 0)
      return { ...page, items: refreshed }
    },
    { enabled: !invalidFilter },
  )

  const detailRes = useCachedResource(
    selected ? `tracker:issue:${selected}` : '',
    () => api.trackerIssue(selected),
    { enabled: Boolean(selected) },
  )
  const commentsRes = useCachedResource(
    selected ? `tracker:comments:${selected}` : '',
    () => api.trackerComments(selected),
    { enabled: Boolean(selected) },
  )
  const transitionsRes = useCachedResource<TrackerTransition[]>(
    selected ? `tracker:transitions:${selected}` : '',
    () => api.trackerTransitions(selected),
    { enabled: Boolean(selected) },
  )

  const items = listRes.data?.items ?? []
  const total = listRes.data?.total ?? 0
  const hasMore = listRes.data?.has_more ?? false
  const detail = detailRes.data ?? null
  const comments = commentsRes.data ?? []
  const transitions = transitionsRes.data ?? []

  const listError = validationError
    || (invalidFilter ? ru.errors.details.tracker_queue_required_for_untagged : '')
    || (listRes.error ? mapApiError(listRes.error) || ru.tracker.loadError : '')
  const detailError = detailRes.error
    ? mapApiError(detailRes.error) || ru.tracker.detailsError
    : ''
  const error = listError || detailError

  const listLoading = listRes.isRevalidating
  const detailLoading = detailRes.isLoading && !detail

  const clearDetail = () => {
    setSelected('')
  }

  const openIssue = useCallback((key: string) => {
    setSelected(key)
  }, [])

  const applyFilters = useCallback((next: IssueFilterValues) => {
    setValidationError('')
    const nextListKey = next.untagged && !next.queue ? '' : listKey(next)
    if (loadedExtent.current?.key !== nextListKey) loadedExtent.current = null
    setFilters(next)
  }, [])

  // Desktop convenience: highlight the first item as soon as it arrives.
  useEffect(() => {
    if (selected || !listRes.data || listRes.data.items.length === 0) return
    if (!isMobileShell()) {
      setSelected(listRes.data.items[0].key)
    }
  }, [listRes.data, selected])

  const currentOwner = `${user?.id}:${currentListKey}:${selected}`
  const owner = useRef(currentOwner)
  useLayoutEffect(() => {
    owner.current = currentOwner
    return () => { owner.current = '' }
  }, [currentOwner])

  const loadMore = async () => {
    if (loadMoreRef.current || listRes.isRevalidating || !listRes.data) return
    loadMoreRef.current = true
    const base = listRes.data
    loadedExtent.current = { key: currentListKey, items: base.items.length + PAGE_SIZE }
    appendRequest.current = { key: currentListKey, base }
    // Route pagination through the same request generation as background reads.
    resourceStore.invalidate(currentListKey)
    resourceStore.set(currentListKey, base, false)
    try {
      await listRes.refresh()
    } finally {
      loadMoreRef.current = false
    }
  }

  const refreshSelected = async () => {
    if (!selected || owner.current !== currentOwner) return
    resourceStore.invalidate(`tracker:issue:${selected}`)
    resourceStore.invalidate(`tracker:comments:${selected}`)
    resourceStore.invalidate(`tracker:transitions:${selected}`)
    await Promise.all([
      detailRes.refresh(),
      commentsRes.refresh(),
      transitionsRes.refresh(),
    ])
  }

  const refreshAll = async () => {
    if (owner.current !== currentOwner) return
    loadedExtent.current = {
      key: currentListKey,
      items: Math.max(
        loadedExtent.current?.key === currentListKey ? loadedExtent.current.items : 0,
        resourceStore.get<ListPage>(currentListKey)?.items.length ?? 0,
      ),
    }
    resourceStore.invalidate(currentListKey)
    await Promise.all([refreshSelected(), listRes.refresh()])
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
          onApply={applyFilters}
          trackerLogin={user?.tracker_login}
        />
        <IssueList
          hasMore={hasMore}
          items={items}
          loading={listLoading}
          onLoadMore={() => void loadMore()}
          onSelect={openIssue}
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

        {detail && <TaskCollaboration issueKey={detail.key} owner={user?.username ?? ''} active canWrite={canWrite} />}

        <IssueDetailPanel
          comments={comments}
          commentsLoading={commentsRes.isLoading && !commentsRes.data}
          currentUser={user?.tracker_login ?? user?.username}
          accountKey={user?.username}
          commentsAsHistory={user?.role === 'mechanic'}
          issue={detail}
          loading={detailLoading}
        />

        {detail && (
          <IssueActionsPanel
            canWrite={canWrite}
            currentUser={user?.tracker_login ?? undefined}
            draftOwner={user?.username}
            issueKey={detail.key}
            issueUrl={detail.url}
            onAssign={async (assignee) => {
              await runTrackerSubmission(user?.username ?? '', detail, 'assign', { assignee }, headers => api.trackerAssign(detail.key, assignee, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshAll()
            }}
            onClose={async () => {
              await runTrackerSubmission(user?.username ?? '', detail, 'close', {}, headers => api.trackerClose(detail.key, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshAll()
            }}
            onComment={async (text) => {
              await runTrackerSubmission(user?.username ?? '', detail, 'comment', { text }, headers => api.trackerComment(detail.key, text, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshSelected()
            }}
            onAttach={async (file) => {
              await runTrackerSubmission(user?.username ?? '', detail, 'attach', await attachmentIdentity(file), headers => api.trackerAttach(detail.key, file, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshAll()
            }}
            onTransition={async (transition) => {
              await runTrackerSubmission(user?.username ?? '', detail, 'transition', { transition }, headers => api.trackerTransition(detail.key, transition, undefined, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshAll()
            }}
            onUnassign={async () => {
              await runTrackerSubmission(user?.username ?? '', detail, 'unassign', {}, headers => api.trackerUnassign(detail.key, headers), () => { if (owner.current !== currentOwner) throw new Error('work_access_changed') })
              await refreshAll()
            }}
            transitions={transitions}
          />
        )}
      </div>
    </section>
  )
}
