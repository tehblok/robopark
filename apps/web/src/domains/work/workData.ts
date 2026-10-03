import { api, type Paged, type TrackerIssue } from '../../api'
import type { WorkUrlState } from './workUrl'

export const WORK_PAGE_SIZE = 50

export type WorkApiClient = Pick<typeof api, 'trackerIssues'>

export function queueDowntimeHours(item: TrackerIssue, now: number): number | null {
  if (item.sla_source !== 'status_history' || !item.queued_at || !Number.isFinite(now)) return null
  const started = Date.parse(item.queued_at)
  return Number.isFinite(started) && started <= now ? (now - started) / 3_600_000 : null
}

function effectiveTimestamp(item: TrackerIssue): number | null {
  if (item.sla_source !== 'status_history' || !item.queued_at?.trim()) return null
  const parsed = Date.parse(item.queued_at)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Tracker normally applies this ordering server-side. Keep it at the UI
 * boundary too: verified queue entries remain oldest first, while entries
 * without a proven transition retain source order rather than inheriting
 * their ticket creation age. Callers retain their original array.
 */
export function oldestFirst(issues: readonly TrackerIssue[]): TrackerIssue[] {
  return issues
    .map((item, index) => ({ item, index, timestamp: effectiveTimestamp(item) }))
    .sort((left, right) => {
      if (left.timestamp != null && right.timestamp != null) {
        return left.timestamp - right.timestamp || left.index - right.index
      }
      if (left.timestamp != null) return -1
      if (right.timestamp != null) return 1
      return left.index - right.index
    })
    .map(({ item }) => item)
}

function pageOffset(page: number): number {
  if (!Number.isSafeInteger(page) || page < 1) return 0
  const offset = (page - 1) * WORK_PAGE_SIZE
  return Number.isSafeInteger(offset) ? offset : 0
}

export function loadWorkPage(
  client: WorkApiClient,
  state: WorkUrlState,
  parkTag: string | undefined,
  queueFirst = false,
): Promise<Paged<TrackerIssue>> {
  const { filters } = state
  const priorityQueue = queueFirst && !filters.status

  return client.trackerIssues({
    queue: filters.queue,
    park: filters.untagged ? undefined : parkTag,
    status: filters.status,
    open_only: true,
    robot: filters.robot,
    assignee: filters.assignee,
    untagged: filters.untagged,
    age_hours: filters.ageHours,
    ...(filters.includeHidden ? { include_hidden: true } : {}),
    ...(state.sync ? { sync_state: state.sync } : {}),
    sort: priorityQueue ? 'queue_first' : 'oldest',
    limit: WORK_PAGE_SIZE,
    offset: pageOffset(state.page),
  }).then((page) => ({ ...page, items: priorityQueue ? page.items : oldestFirst(page.items) }))
}
