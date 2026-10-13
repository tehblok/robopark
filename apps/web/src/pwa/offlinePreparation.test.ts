import { describe, expect, it, vi } from 'vitest'
import type { Park, Paged, RobotRegistry, TrackerIssue, User } from '../api'
import { prepareOfflineWorkspace } from './offlinePreparation'

const parks: Park[] = [
  { id: 7, name: 'Seven', tag: 'seven', timezone: 'Europe/Moscow' },
  { id: 8, name: 'Eight', tag: 'eight', timezone: 'Europe/Moscow' },
]
const user = { id: 11, username: 'operator', role: 'operator', access_status: 'approved', permissions: ['tracker.read'], parks } as User
const emptyTasks: Paged<TrackerIssue> = { items: [], total: 0, limit: 100, offset: 0, has_more: false }
const emptyRobots: RobotRegistry = { items: [], total: 0, offset: 0, limit: 100, has_more: false, partial: false, source_complete: true, source: 'scoped_tracker_issues', park_id: 7 }

describe('offline preparation', () => {
  it('does not report ready when a snapshot write is refused', async () => {
    const setBatch = vi.fn(async () => false)
    await expect(prepareOfflineWorkspace({ user, selectedParkIds: [7], store: { setBatch }, api: {
      trackerIssues: async () => emptyTasks, robotRegistry: async () => emptyRobots,
    } })).rejects.toThrow('offline_preparation_failed')
  })
  it('prepares only authorized selected parks with hard request and item bounds', async () => {
    const setBatch = vi.fn(async (_values: import('../lib/indexedResourceStore').ResourceBatchEntry[]) => true)
    const trackerIssues = vi.fn(async () => emptyTasks)
    const robotRegistry = vi.fn(async ({ park_id }: { park_id?: number }) => ({ ...emptyRobots, park_id: park_id ?? null }))
    const result = await prepareOfflineWorkspace({ user, selectedParkIds: [8, 999, 7, 8], api: { trackerIssues, robotRegistry }, store: { setBatch } })

    expect(result).toMatchObject({ state: 'ready', parkCount: 2, taskCount: 0, robotCount: 0, complete: true })
    expect(trackerIssues).toHaveBeenCalledTimes(2)
    expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ limit: 50, offset: 0, open_only: true }))
    expect(robotRegistry).toHaveBeenCalledTimes(2)
    expect(robotRegistry).toHaveBeenCalledWith(expect.objectContaining({ limit: 100, offset: 0 }))
    expect(setBatch).toHaveBeenCalledTimes(1)
    expect(setBatch.mock.calls[0][0]).toHaveLength(5)
    expect(setBatch.mock.calls[0][0].every(value => value.retentionMs === 72 * 60 * 60 * 1000)).toBe(true)
  })

  it('keeps the previous prepared data when refresh fails', async () => {
    const setBatch = vi.fn(async (_values: import('../lib/indexedResourceStore').ResourceBatchEntry[]) => true)
    await expect(prepareOfflineWorkspace({
      user, selectedParkIds: [7], api: {
        trackerIssues: vi.fn(async () => { throw new TypeError('offline') }),
        robotRegistry: vi.fn(async () => emptyRobots),
      }, store: { setBatch }, previous: { version: 1, state: 'ready', preparedAt: 100, parkCount: 1, taskCount: 2, robotCount: 3, complete: true },
    })).resolves.toMatchObject({ state: 'stale', preparedAt: 100, parkCount: 1, taskCount: 2, robotCount: 3 })
    expect(setBatch).not.toHaveBeenCalled()
  })

  it('does not overwrite queued work while refreshing read snapshots', async () => {
    const queue = { id: 'queued-action' }
    const store = { setBatch: vi.fn(async (_values: import('../lib/indexedResourceStore').ResourceBatchEntry[]) => true) }
    await prepareOfflineWorkspace({ user, selectedParkIds: [7], api: {
      trackerIssues: vi.fn(async () => emptyTasks), robotRegistry: vi.fn(async () => emptyRobots),
    }, store })
    expect(queue).toEqual({ id: 'queued-action' })
    expect(store.setBatch.mock.calls.flatMap(([values]) => values).every(({ key }) => !String(key).includes('action') && !String(key).includes('media'))).toBe(true)
  })
})
