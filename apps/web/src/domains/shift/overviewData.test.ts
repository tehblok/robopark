import { describe, expect, it, vi } from 'vitest'
import type { Blocker, DashboardSummary, Park, TrackerIssue, User } from '../../api'
import { canLoadOverviewQueue, loadOverview, type OverviewApiClient } from './overviewData'

const alpha: Park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK', is_active: true }
const beta: Park = { id: 8, name: 'Юг', tag: 'Beta', tracker_queue: 'ROBOPARK', is_active: true }

const summary = (parkId: number): DashboardSummary => ({
  park_id: parkId,
  generated_at: '2026-09-02T09:00:00Z',
  arrived: 1,
  done: 2,
  queued: 3,
  in_transit: 1,
  moving: [],
})

function user(role: string, permissions: string[] = ['nav.dashboard', 'tracker.read']): User {
  return { id: 1, username: role, role, access_status: 'approved', permissions, parks: [alpha, beta] }
}

const blockers: Blocker[] = Array.from({ length: 7 }, (_, index) => ({
  key: `ROBOPARK-${index + 1}`,
  summary: `Робот ${index + 1} остановился`,
  status: 'Open',
  robot: String(index + 1),
  created_at: '2026-09-02T08:00:00Z',
  hours_created: '1',
  url: `https://tracker.example/ROBOPARK-${index + 1}`,
  bucket: 'queued',
}))
const trackerIssues: TrackerIssue[] = [
  { key: 'ROBOPARK-42', summary: 'Проверить робота', status: 'Open', url: 'https://tracker.example/ROBOPARK-42' },
]

function client(): OverviewApiClient {
  return {
    dashboardSummary: vi.fn(async (parkId: number) => summary(parkId)),
    mechanicTasks: vi.fn(async () => ({ park_tag: 'Alpha', status: 'all', counts: {}, items: blockers })),
    operatorBlockers: vi.fn(async () => ({ park_id: 7, park_tag: 'Alpha', status: 'all', counts: {}, items: blockers })),
    trackerIssues: vi.fn(async () => ({ items: trackerIssues, total: 1, limit: 5, offset: 0, has_more: false })),
  }
}

function expectNoQueueRequests(apiClient: OverviewApiClient) {
  expect(apiClient.mechanicTasks).not.toHaveBeenCalled()
  expect(apiClient.operatorBlockers).not.toHaveBeenCalled()
  expect(apiClient.trackerIssues).not.toHaveBeenCalled()
}

describe('canLoadOverviewQueue', () => {
  it.each(['mechanic', 'operator'])('allows the exact %s specialized role in a configured park', (role) => {
    expect(canLoadOverviewQueue(user(role, []), alpha)).toBe(true)
  })

  it.each(['Mechanic', 'operator_custom', 'admin', 'field_lead'])('does not infer Tracker permission from %s', (role) => {
    expect(canLoadOverviewQueue(user(role, ['nav.dashboard', 'nav.tasks', 'tracker.write']), alpha)).toBe(false)
  })

  it('denies a custom role with no permission list', () => {
    expect(canLoadOverviewQueue({ ...user('field_lead'), permissions: undefined }, alpha)).toBe(false)
  })

  it.each(['admin', 'field_lead'])('allows explicit tracker.read for %s with trimmed configuration', (role) => {
    expect(canLoadOverviewQueue(user(role), {
      ...alpha, tracker_queue: ' ROBOPARK ', tag: ' Alpha ',
    })).toBe(true)
  })

  it.each(['mechanic', 'operator', 'admin', 'field_lead'])('denies %s when either queue or tag is missing', (role) => {
    expect(canLoadOverviewQueue(user(role), { ...alpha, tracker_queue: null })).toBe(false)
    expect(canLoadOverviewQueue(user(role), { ...alpha, tracker_queue: ' ' })).toBe(false)
    expect(canLoadOverviewQueue(user(role), { ...alpha, tag: '' })).toBe(false)
    expect(canLoadOverviewQueue(user(role), { ...alpha, tag: ' ' })).toBe(false)
  })
})

describe('loadOverview', () => {
  it.each(['mechanic', 'operator'])('loads and bounds the %s selected-park queue', async (role) => {
    const apiClient = client()
    const result = await loadOverview(apiClient, user(role), alpha, [alpha, beta])

    expect(result).toEqual({
      kind: 'park',
      park: alpha,
      summary: summary(7),
      issues: [blockers[0], blockers[1], blockers[2], blockers[3], blockers[4]],
    })
    expect(apiClient.dashboardSummary).toHaveBeenCalledExactlyOnceWith(7)
    if (role === 'mechanic') {
      expect(apiClient.mechanicTasks).toHaveBeenCalledExactlyOnceWith('all', 7)
      expect(apiClient.operatorBlockers).not.toHaveBeenCalled()
    } else {
      expect(apiClient.operatorBlockers).toHaveBeenCalledExactlyOnceWith(7, 'all')
      expect(apiClient.mechanicTasks).not.toHaveBeenCalled()
    }
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
    expect(blockers).toHaveLength(7)
  })

  it.each(['admin', 'field_lead'])('loads the %s generic queue only with tracker.read', async (role) => {
    const apiClient = client()
    const selectedPark = { ...beta, tag: ' Beta ', tracker_queue: ' ROBOPARK ' }
    const result = await loadOverview(apiClient, user(role), selectedPark, [alpha, selectedPark])

    expect(result).toEqual({ kind: 'park', park: selectedPark, summary: summary(8), issues: trackerIssues })
    expect(apiClient.dashboardSummary).toHaveBeenCalledExactlyOnceWith(8)
    expect(apiClient.trackerIssues).toHaveBeenCalledExactlyOnceWith({
      queue: 'ROBOPARK', park: 'Beta', sort: 'oldest', limit: 5, offset: 0,
    })
    expect(apiClient.mechanicTasks).not.toHaveBeenCalled()
    expect(apiClient.operatorBlockers).not.toHaveBeenCalled()
  })

  it.each([
    ['admin', ['nav.dashboard']],
    ['field_lead', ['nav.dashboard']],
    ['field_lead', ['nav.dashboard', 'tracker.write', 'nav.admin.tracker']],
  ])('loads a summary without a Tracker request for %s lacking tracker.read', async (role, permissions) => {
    const apiClient = client()
    const result = await loadOverview(apiClient, user(role as string, permissions as string[]), alpha, [alpha, beta])

    expect(result).toEqual({ kind: 'park', park: alpha, summary: summary(7), issues: [] })
    expect(apiClient.dashboardSummary).toHaveBeenCalledExactlyOnceWith(7)
    expectNoQueueRequests(apiClient)
  })

  it('does not infer tracker.read when the permission list is absent', async () => {
    const apiClient = client()
    const currentUser = { ...user('field_lead'), permissions: undefined }

    expect(await loadOverview(apiClient, currentUser, alpha, [alpha])).toEqual({
      kind: 'park', park: alpha, summary: summary(7), issues: [],
    })
    expectNoQueueRequests(apiClient)
  })

  it('loads only active parks from the authenticated fleet scope', async () => {
    const apiClient = client()
    const inactive = { ...beta, is_active: false }
    const activeWithoutFlag = { ...alpha, id: 9, name: 'Запад', is_active: undefined }

    expect(await loadOverview(apiClient, user('royal'), null, [alpha, inactive, activeWithoutFlag])).toEqual({
      kind: 'fleet',
      summaries: [
        { park: alpha, summary: summary(7) },
        { park: activeWithoutFlag, summary: summary(9) },
      ],
    })
    expect(apiClient.dashboardSummary).toHaveBeenCalledTimes(2)
    expect(apiClient.dashboardSummary).toHaveBeenNthCalledWith(1, 7)
    expect(apiClient.dashboardSummary).toHaveBeenNthCalledWith(2, 9)
    expectNoQueueRequests(apiClient)
  })

  it('returns an empty fleet without making requests when no active parks exist', async () => {
    const apiClient = client()
    expect(await loadOverview(apiClient, user('royal'), alpha, [{ ...alpha, is_active: false }])).toEqual({
      kind: 'fleet', summaries: [],
    })
    expect(apiClient.dashboardSummary).not.toHaveBeenCalled()
    expectNoQueueRequests(apiClient)
  })

  it('makes zero requests for a driver even without a selected park', async () => {
    const apiClient = client()
    expect(await loadOverview(apiClient, user('driver'), null, [alpha, beta])).toEqual({ kind: 'driver' })
    expect(apiClient.dashboardSummary).not.toHaveBeenCalled()
    expectNoQueueRequests(apiClient)
  })

  it.each(['mechanic', 'operator', 'admin', 'field_lead'])('fails before requesting data without a selected park for %s', async (role) => {
    const apiClient = client()
    await expect(loadOverview(apiClient, user(role), null, [alpha, beta])).rejects.toThrow('overview_park_required')
    expect(apiClient.dashboardSummary).not.toHaveBeenCalled()
    expectNoQueueRequests(apiClient)
  })

  describe.each(['mechanic', 'operator', 'admin', 'field_lead'])('%s readiness', (role) => {
    it.each([
      { name: 'missing queue', overrides: { tracker_queue: null } },
      { name: 'blank queue', overrides: { tracker_queue: '   ' } },
      { name: 'empty park tag', overrides: { tag: '' } },
      { name: 'blank park tag', overrides: { tag: '   ' } },
    ])('preserves the summary without queue requests for $name', async ({ overrides }) => {
      const apiClient = client()
      const selectedPark = { ...alpha, ...overrides }

      expect(await loadOverview(apiClient, user(role), selectedPark, [selectedPark])).toEqual({
        kind: 'park', park: selectedPark, summary: summary(7), issues: [],
      })
      expect(apiClient.dashboardSummary).toHaveBeenCalledExactlyOnceWith(7)
      expectNoQueueRequests(apiClient)
    })
  })

  it('propagates a summary failure without starting a queue request', async () => {
    const apiClient = client()
    const failure = new Error('upstream')
    vi.mocked(apiClient.dashboardSummary).mockRejectedValue(failure)
    await expect(loadOverview(apiClient, user('operator'), alpha, [alpha])).rejects.toBe(failure)
    expectNoQueueRequests(apiClient)
  })
})
