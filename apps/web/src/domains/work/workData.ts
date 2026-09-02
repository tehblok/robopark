import { api, type Paged, type TrackerIssue } from '../../api'
import type { WorkUrlState } from './workUrl'

export const WORK_PAGE_SIZE = 50

export type WorkApiClient = Pick<typeof api, 'trackerIssues'>

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
    robot: filters.robot,
    assignee: filters.assignee,
    untagged: filters.untagged,
    age_hours: filters.ageHours,
    sort: state.sort,
    limit: WORK_PAGE_SIZE,
    offset: pageOffset(state.page),
  })
}
