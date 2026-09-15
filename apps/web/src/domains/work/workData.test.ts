import { describe, expect, it, vi } from 'vitest'
import type { Paged, TrackerIssue } from '../../api'
import { loadWorkPage, oldestFirst, WORK_PAGE_SIZE, type WorkApiClient } from './workData'

function emptyPage(offset: number): Paged<TrackerIssue> {
  return {
    items: [],
    total: 0,
    limit: WORK_PAGE_SIZE,
    offset,
    has_more: false,
  }
}

describe('loadWorkPage', () => {
  it('returns a new oldest-first queue by queued timestamp', () => {
    const items: TrackerIssue[] = [
      { key: 'ROBOPARK-3', summary: 'newest', status: 'Open', queued_at: '2026-09-03T09:00:00Z', url: '' },
      { key: 'ROBOPARK-1', summary: 'oldest', status: 'Open', queued_at: '2026-09-01T09:00:00Z', url: '' },
      { key: 'ROBOPARK-2', summary: 'middle', status: 'Open', queued_at: '2026-09-02T09:00:00Z', url: '' },
    ]

    expect(oldestFirst(items).map((item) => item.key)).toEqual([
      'ROBOPARK-1',
      'ROBOPARK-2',
      'ROBOPARK-3',
    ])
    expect(items.map((item) => item.key)).toEqual([
      'ROBOPARK-3',
      'ROBOPARK-1',
      'ROBOPARK-2',
    ])
  })

  it('falls back to created timestamps and leaves malformed timestamps stable', () => {
    const items: TrackerIssue[] = [
      { key: 'UNKNOWN-A', summary: '', status: 'Open', created_at: null, hours_created: '', url: '' },
      { key: 'DATED', summary: '', status: 'Open', queued_at: '2026-01-06T09:00:00Z', created_at: '2026-01-01T09:00:00Z', url: '' },
      { key: 'CREATED', summary: '', status: 'Open', queued_at: 'not-a-date', created_at: '2026-01-05T09:00:00Z', url: '' },
      { key: 'UNKNOWN-B', summary: '', status: 'Open', created_at: null, hours_created: 'NaN', url: '' },
      { key: 'UNKNOWN-C', summary: '', status: 'Open', created_at: '', hours_created: 'Infinity', url: '' },
    ]

    expect(oldestFirst(items).map((item) => item.key)).toEqual([
      'CREATED',
      'DATED',
      'UNKNOWN-A',
      'UNKNOWN-B',
      'UNKNOWN-C',
    ])
    expect(items.map((item) => item.key)).toEqual([
      'UNKNOWN-A',
      'DATED',
      'CREATED',
      'UNKNOWN-B',
      'UNKNOWN-C',
    ])
  })

  it('preserves input order when queued timestamps tie', () => {
    const items: TrackerIssue[] = [
      { key: 'ROBOPARK-2', summary: '', status: 'Open', queued_at: '2026-01-01T09:00:00Z', url: '' },
      { key: 'ROBOPARK-1', summary: '', status: 'Open', queued_at: '2026-01-01T09:00:00Z', url: '' },
    ]

    expect(oldestFirst(items).map(item => item.key)).toEqual(['ROBOPARK-2', 'ROBOPARK-1'])
  })

  it('maps filters and pagination while forcing oldest even for legacy caller state', async () => {
    const result = emptyPage(WORK_PAGE_SIZE * 2)
    const trackerIssues = vi.fn(async () => result)
    const client = { trackerIssues } as WorkApiClient

    await expect(
      loadWorkPage(
        client,
        {
          filters: {
            queue: 'ROBOPARK',
            status: 'open',
            robot: '447',
            assignee: 'ivan',
            ageHours: 24,
          },
          sort: 'newest',
          page: 3,
        },
        'Alpha',
      ),
    ).resolves.toEqual(result)

    expect(trackerIssues).toHaveBeenCalledWith({
      queue: 'ROBOPARK',
      park: 'Alpha',
      status: 'open',
      robot: '447',
      assignee: 'ivan',
      untagged: undefined,
      age_hours: 24,
      open_only: true,
      sort: 'oldest',
      limit: 50,
      offset: 100,
    })
  })

  it(
    'omits the Tracker park tag for untagged work and keeps oldest pagination explicit',
    async () => {
      const result = emptyPage(0)
      const trackerIssues = vi.fn(async () => result)
      const client = { trackerIssues } as WorkApiClient

      await loadWorkPage(
        client,
        {
          filters: { queue: 'ROBOPARK', untagged: true },
          sort: 'oldest',
          page: 1,
        },
        'Alpha',
      )

      expect(trackerIssues).toHaveBeenCalledWith({
        queue: 'ROBOPARK',
        park: undefined,
        status: undefined,
        robot: undefined,
        assignee: undefined,
        untagged: true,
        age_hours: undefined,
        open_only: true,
        sort: 'oldest',
        limit: 50,
        offset: 0,
      })
    },
  )

  it(
    'fails closed to the first page when a supplied page would overflow the offset',
    async () => {
      const trackerIssues = vi.fn(async () => emptyPage(0))
      const client = { trackerIssues } as WorkApiClient

      await loadWorkPage(
        client,
        {
          filters: { queue: 'ROBOPARK' },
          sort: 'oldest',
          page: Number.MAX_SAFE_INTEGER,
        },
        'Alpha',
      )

      expect(trackerIssues).toHaveBeenCalledWith(
        expect.objectContaining({
          limit: 50,
          offset: 0,
        }),
      )
    },
  )
})
