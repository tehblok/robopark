import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  buildWorkSearch,
  parseWorkUrl,
  readWorkScroll,
  saveWorkScroll,
  workIssueHref,
  workListHref,
} from './workUrl'

const defaults = { queue: 'ROBOPARK' }

describe('work URL state', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  it('parses filters, sort and a positive page', () => {
    const state = parseWorkUrl(
      new URLSearchParams({
        park: '7',
        queue: 'OPS',
        status: 'open',
        robot: '447',
        assignee: 'ivan',
        untagged: '1',
        age: '24',
        sort: 'newest',
        page: '3',
      }),
      defaults,
    )

    expect(state).toEqual({
      filters: {
        queue: 'OPS',
        status: 'open',
        robot: '447',
        assignee: 'ivan',
        untagged: true,
        ageHours: 24,
      },
      sort: 'newest',
      page: 3,
    })
  })

  it(
    'normalizes invalid numeric values and leaves the Foundation park key untouched',
    () => {
      const params = new URLSearchParams(
        'park=7&page=9007199254740992&age=1.5&sort=random',
      )

      expect(parseWorkUrl(params, defaults)).toEqual({
        filters: { queue: 'ROBOPARK' },
        sort: 'oldest',
        page: 1,
      })
      expect(params.get('park')).toBe('7')
    },
  )

  it.each(['0', '-2', '1.5', '1e2', ' 2', '+2', '9007199254740992'])(
    'fails closed for invalid page and age value %j',
    (raw) => {
      const params = new URLSearchParams()
      params.set('page', raw)
      params.set('age', raw)

      expect(parseWorkUrl(params, defaults)).toEqual({
        filters: { queue: 'ROBOPARK' },
        sort: 'oldest',
        page: 1,
      })
    },
  )

  it('rejects a safe integer page whose page offset would be unsafe', () => {
    const params = new URLSearchParams({ page: String(Number.MAX_SAFE_INTEGER) })

    expect(parseWorkUrl(params, defaults).page).toBe(1)
    expect(
      buildWorkSearch(
        {
          filters: { queue: 'ROBOPARK' },
          sort: 'oldest',
          page: Number.MAX_SAFE_INTEGER,
        },
        7,
      ),
    ).toBe('?park=7&queue=ROBOPARK')
  })

  it.each([0, -1, 1.5, Number.POSITIVE_INFINITY, Number.NaN, 2 ** 53])(
    'omits invalid Foundation park id %s',
    (parkId) => {
      const state = parseWorkUrl(new URLSearchParams(), defaults)

      expect(buildWorkSearch(state, parkId)).toBe('?queue=ROBOPARK')
    },
  )

  it.each([0, -1, 1.5, Number.POSITIVE_INFINITY, Number.NaN, 2 ** 53])(
    'omits invalid age filter %s',
    (ageHours) => {
      expect(
        buildWorkSearch(
          {
            filters: { queue: 'ROBOPARK', ageHours },
            sort: 'oldest',
            page: 1,
          },
          7,
        ),
      ).toBe('?park=7&queue=ROBOPARK')
    },
  )

  it('serializes the largest safe Foundation park id and age filter', () => {
    expect(
      buildWorkSearch(
        {
          filters: {
            queue: 'ROBOPARK',
            ageHours: Number.MAX_SAFE_INTEGER,
          },
          sort: 'oldest',
          page: 1,
        },
        Number.MAX_SAFE_INTEGER,
      ),
    ).toBe(
      '?park=9007199254740991&queue=ROBOPARK&age=9007199254740991',
    )
  })

  it('serializes stable links for list and detail', () => {
    const state = parseWorkUrl(
      new URLSearchParams('status=open&sort=newest&page=2'),
      defaults,
    )

    expect(buildWorkSearch(state, 7)).toBe(
      '?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
    expect(workListHref(state, 7)).toBe(
      '/work?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
    expect(workIssueHref('ROBOPARK-42', state, 7)).toBe(
      '/work/ROBOPARK-42?park=7&queue=ROBOPARK&status=open&sort=newest&page=2',
    )
  })

  it('omits default sort and page from the canonical href', () => {
    const state = parseWorkUrl(
      new URLSearchParams('sort=oldest&page=01'),
      defaults,
    )

    expect(buildWorkSearch(state, 7)).toBe('?park=7&queue=ROBOPARK')
    expect(workIssueHref('ROBOPARK-42', state, 7)).toBe(
      '/work/ROBOPARK-42?park=7&queue=ROBOPARK',
    )
  })

  it('round-trips canonical state without changing its serialization', () => {
    const initial = parseWorkUrl(
      new URLSearchParams('robot=447&age=24&sort=newest&page=2'),
      defaults,
    )
    const search = buildWorkSearch(initial, 7)

    expect(parseWorkUrl(new URLSearchParams(search), defaults)).toEqual(initial)
    expect(buildWorkSearch(initial, 7)).toBe(
      '?park=7&queue=ROBOPARK&robot=447&age=24&sort=newest&page=2',
    )
  })

  it('encodes an issue key without changing the stable query order', () => {
    const state = parseWorkUrl(new URLSearchParams('status=open'), defaults)

    expect(workIssueHref('ROBOPARK/42 ?#', state, 7)).toBe(
      '/work/ROBOPARK%2F42%20%3F%23?park=7&queue=ROBOPARK&status=open',
    )
  })

  it('keeps scroll positions isolated by user and canonical query', () => {
    saveWorkScroll(7, '?park=7&status=open', 480)

    expect(readWorkScroll(7, '?park=7&status=open')).toBe(480)
    expect(readWorkScroll(8, '?park=7&status=open')).toBe(0)
    expect(readWorkScroll(7, '?park=7&status=closed')).toBe(0)
  })

  it('does not break navigation when sessionStorage writes are blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError')
    })

    expect(() => saveWorkScroll(7, '?park=7', 480)).not.toThrow()
  })

  it('falls back to zero when sessionStorage reads are blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Blocked', 'SecurityError')
    })

    expect(readWorkScroll(7, '?park=7')).toBe(0)
  })

  it.each(['corrupt', 'Infinity', '-1'])(
    'falls back to zero for invalid stored scroll value %j',
    (raw) => {
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue(raw)

      expect(readWorkScroll(7, '?park=7')).toBe(0)
    },
  )
})
