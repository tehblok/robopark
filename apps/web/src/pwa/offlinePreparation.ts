import type { Paged, RobotRegistry, TrackerIssue, User } from '../api'
import { workListCacheKey } from '../domains/work/workCacheKey'
import { oldestFirst, WORK_PAGE_SIZE } from '../domains/work/workData'
import type { ResourceBatchEntry } from '../lib/indexedResourceStore'

export const OFFLINE_PREPARATION_VERSION = 1
export const OFFLINE_PREPARATION_MAX_PARKS = 12
export const OFFLINE_PREPARATION_MAX_TASKS_PER_PARK = WORK_PAGE_SIZE
export const OFFLINE_PREPARATION_MAX_ROBOTS_PER_PARK = 100
export const OFFLINE_PREPARATION_FRESH_MS = 12 * 60 * 60 * 1000
export const OFFLINE_PREPARATION_RETENTION_MS = 72 * 60 * 60 * 1000

export type OfflinePreparationState = {
  version: 1
  state: 'ready' | 'stale'
  preparedAt: number
  parkCount: number
  taskCount: number
  robotCount: number
  complete: boolean
}

type PreparationApi = {
  trackerIssues(params: { queue?: string; park?: string; status?: string; open_only: boolean; sort: 'oldest'; limit: number; offset: number }): Promise<Paged<TrackerIssue>>
  robotRegistry(params: { park_id: number; limit: number; offset: number }): Promise<RobotRegistry>
}
type PreparationStore = { setBatch(values: ResourceBatchEntry[]): Promise<boolean> }

export function offlinePreparationKey(accountId: number): string { return `offline-preparation:${accountId}:state` }
export function offlinePreparedRobotsKey(accountId: number, parkId: number): string { return `offline-preparation:${accountId}:park:${parkId}:robots` }

async function boundedMap<T, R>(items: T[], concurrency: number, operation: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (next < items.length) {
      const index = next++
      results[index] = await operation(items[index])
    }
  }))
  return results
}

export async function prepareOfflineWorkspace({ user, selectedParkIds, api, store, previous, now = Date.now }:
  { user: User; selectedParkIds: number[]; api: PreparationApi; store: PreparationStore; previous?: OfflinePreparationState; now?: () => number }): Promise<OfflinePreparationState> {
  const authorized = new Map(user.parks.map(park => [park.id, park]))
  const parks = [...new Set(selectedParkIds)].flatMap(id => {
    const park = authorized.get(id)
    return park ? [park] : []
  }).slice(0, OFFLINE_PREPARATION_MAX_PARKS)
  const preparedAt = now()
  try {
    const snapshots = await boundedMap(parks, 3, async park => {
      const [tasks, robots] = await Promise.all([
        api.trackerIssues({ queue: park.tracker_queue?.trim() || undefined, park: park.tag, status: user.role === 'driver' ? 'new' : 'queued', open_only: true, sort: 'oldest', limit: OFFLINE_PREPARATION_MAX_TASKS_PER_PARK, offset: 0 }),
        api.robotRegistry({ park_id: park.id, limit: OFFLINE_PREPARATION_MAX_ROBOTS_PER_PARK, offset: 0 }),
      ])
      return { park, tasks: { ...tasks, items: oldestFirst(tasks.items) }, robots }
    })
    const state: OfflinePreparationState = {
      version: 1, state: 'ready', preparedAt, parkCount: snapshots.length,
      taskCount: snapshots.reduce((total, item) => total + item.tasks.items.length, 0),
      robotCount: snapshots.reduce((total, item) => total + item.robots.items.length, 0),
      complete: snapshots.every(item => !item.tasks.has_more && !item.robots.has_more),
    }
    const publication: ResourceBatchEntry[] = []
    for (const snapshot of snapshots) {
      const workState = {
        filters: { ...(snapshot.park.tracker_queue?.trim() ? { queue: snapshot.park.tracker_queue.trim() } : {}), status: user.role === 'driver' ? 'new' : 'queued' },
        sort: 'oldest' as const, page: 1,
      }
      publication.push({ key: workListCacheKey(user, snapshot.park, workState), data: snapshot.tasks, updatedAt: preparedAt, retentionMs: OFFLINE_PREPARATION_RETENTION_MS },
        { key: offlinePreparedRobotsKey(user.id, snapshot.park.id), data: snapshot.robots, updatedAt: preparedAt, retentionMs: OFFLINE_PREPARATION_RETENTION_MS })
    }
    publication.push({ key: offlinePreparationKey(user.id), data: state, updatedAt: preparedAt, retentionMs: OFFLINE_PREPARATION_RETENTION_MS })
    if (!await store.setBatch(publication)) throw new Error('offline_snapshot_not_saved')
    return state
  } catch {
    if (previous) return { ...previous, state: 'stale' }
    throw new Error('offline_preparation_failed')
  }
}
