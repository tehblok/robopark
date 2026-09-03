import type { api, Blocker, DashboardSummary, Park, TrackerIssue, User } from '../../api'

export type OverviewApiClient = Pick<
  typeof api,
  'dashboardSummary' | 'mechanicTasks' | 'operatorBlockers' | 'trackerIssues'
>

export type OverviewPayload =
  | { kind: 'driver' }
  | { kind: 'park'; park: Park; summary: DashboardSummary; issues: Array<Blocker | TrackerIssue> }
  | { kind: 'fleet'; summaries: Array<{ park: Park; summary: DashboardSummary }> }

export function canLoadOverviewQueue(user: User, park: Park): boolean {
  if (!park.tracker_queue?.trim() || !park.tag?.trim()) return false
  return user.role === 'mechanic'
    || user.role === 'operator'
    || (user.permissions ?? []).includes('tracker.read')
}

export async function loadOverview(
  client: OverviewApiClient,
  user: User,
  selectedPark: Park | null,
  parks: Park[],
): Promise<OverviewPayload> {
  if (user.role === 'driver') return { kind: 'driver' }
  if (user.role === 'royal') {
    const summaries = await Promise.all(
      parks
        .filter((park) => park.is_active !== false)
        .map(async (park) => ({ park, summary: await client.dashboardSummary(park.id) })),
    )
    return { kind: 'fleet', summaries }
  }
  if (!selectedPark) throw new Error('overview_park_required')

  const summary = await client.dashboardSummary(selectedPark.id)
  if (!canLoadOverviewQueue(user, selectedPark)) {
    return { kind: 'park', park: selectedPark, summary, issues: [] }
  }
  if (user.role === 'mechanic') {
    const queue = await client.mechanicTasks('all', selectedPark.id)
    return { kind: 'park', park: selectedPark, summary, issues: queue.items.slice(0, 5) }
  }
  if (user.role === 'operator') {
    const queue = await client.operatorBlockers(selectedPark.id, 'all')
    return { kind: 'park', park: selectedPark, summary, issues: queue.items.slice(0, 5) }
  }
  const queue = await client.trackerIssues({
    queue: selectedPark.tracker_queue?.trim(),
    park: selectedPark.tag?.trim(),
    sort: 'oldest',
    limit: 5,
    offset: 0,
  })
  return { kind: 'park', park: selectedPark, summary, issues: queue.items }
}
