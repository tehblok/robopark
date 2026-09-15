import { api, type Paged, type TrackerIssue } from '../../api'
import type { WorkUrlState } from './workUrl'

export const WORK_PAGE_SIZE = 50

export type WorkApiClient = Pick<typeof api, 'trackerIssues'>

function effectiveTimestamp(item: TrackerIssue): number | null {
  for (const value of [item.queued_at, item.created_at]) {
    if (!value?.trim()) continue
    const parsed = Date.parse(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

/**
 * Tracker normally applies this ordering server-side. Keep it at the UI
 * boundary too: an out-of-order upstream page must not turn the operational
 * queue into newest-first, and callers retain their original array.
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
): Promise<Paged<TrackerIssue>> {
  const { filters } = state

  return client.trackerIssues({
    queue: filters.queue,
    park: filters.untagged ? undefined : parkTag,
    status: filters.status,
    open_only: true,
    robot: filters.robot,
    assignee: filters.assignee,
    untagged: filters.untagged,
    age_hours: filters.ageHours,
    sort: 'oldest',
    limit: WORK_PAGE_SIZE,
    offset: pageOffset(state.page),
  }).then((page) => ({ ...page, items: oldestFirst(page.items) }))
}
