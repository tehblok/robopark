import { describe, expect, it, vi } from 'vitest'
import type { Paged, TrackerIssue } from '../../api'
import { loadWorkPage, WORK_PAGE_SIZE, type WorkApiClient } from './workData'

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
  it('maps canonical URL state and Tracker park tag to pagination', async () => {
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
    ).resolves.toBe(result)

    expect(trackerIssues).toHaveBeenCalledWith({
      queue: 'ROBOPARK',
      park: 'Alpha',
      status: 'open',
      robot: '447',
      assignee: 'ivan',
      untagged: undefined,
      age_hours: 24,
      sort: 'newest',
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
