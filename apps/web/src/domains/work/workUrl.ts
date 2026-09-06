export type WorkSort = 'oldest' | 'newest'

export type WorkFilters = {
  queue?: string
  status?: string
  robot?: string
  assignee?: string
  untagged?: boolean
  ageHours?: number
}

export type WorkUrlState = {
  filters: WorkFilters
  sort: WorkSort
  page: number
}

export type WorkDefaults = Pick<WorkFilters, 'queue' | 'status'>

const WORK_PAGE_SIZE_FOR_OFFSET = 50
const MAX_WORK_PAGE =
  Math.floor(Number.MAX_SAFE_INTEGER / WORK_PAGE_SIZE_FOR_OFFSET) + 1

function text(params: URLSearchParams, key: string): string | undefined {
  return params.get(key)?.trim() || undefined
}

function isPositiveSafeInteger(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0
}

function positiveInteger(raw: string | null): number | undefined {
  if (!raw || !/^\d+$/.test(raw)) return undefined
  const value = Number(raw)
  return isPositiveSafeInteger(value) ? value : undefined
}

function pageNumber(raw: string | null): number | undefined {
  const value = positiveInteger(raw)
  return value != null && value <= MAX_WORK_PAGE ? value : undefined
}

function serializablePage(value: number): boolean {
  return isPositiveSafeInteger(value) && value > 1 && value <= MAX_WORK_PAGE
}

export function parseWorkUrl(
  params: URLSearchParams,
  defaults: WorkDefaults,
): WorkUrlState {
  const queue = defaults.queue
  const rawStatus = text(params, 'status')
  const completedStatus = ['closed', 'resolved'].includes(rawStatus?.toLowerCase() ?? '')
  const status = completedStatus || rawStatus === 'all'
    ? undefined
    : rawStatus ?? defaults.status ?? 'queued'
  const robot = text(params, 'robot')
  const assignee = text(params, 'assignee')
  const untagged = params.get('untagged') === '1'
  const ageHours = positiveInteger(params.get('age'))

  return {
    filters: {
      ...(queue ? { queue } : {}),
      ...(status ? { status } : {}),
      ...(robot ? { robot } : {}),
      ...(assignee ? { assignee } : {}),
      ...(untagged ? { untagged: true } : {}),
      ...(ageHours ? { ageHours } : {}),
    },
    sort: 'oldest',
    page: completedStatus ? 1 : pageNumber(params.get('page')) ?? 1,
  }
}

export function buildWorkSearch(state: WorkUrlState, parkId: number | null): string {
  const params = new URLSearchParams()
  const { filters } = state

  if (parkId != null && isPositiveSafeInteger(parkId)) {
    params.set('park', String(parkId))
  }
  if (filters.queue) params.set('queue', filters.queue)
  params.set('status', filters.status || 'all')
  if (filters.robot) params.set('robot', filters.robot)
  if (filters.assignee) params.set('assignee', filters.assignee)
  if (filters.untagged) params.set('untagged', '1')
  if (
    filters.ageHours != null &&
    isPositiveSafeInteger(filters.ageHours)
  ) {
    params.set('age', String(filters.ageHours))
  }
  if (serializablePage(state.page)) params.set('page', String(state.page))

  const query = params.toString()
  return query ? `?${query}` : ''
}

export function workListHref(state: WorkUrlState, parkId: number | null): string {
  return `/work${buildWorkSearch(state, parkId)}`
}

export function workIssueHref(
  issueKey: string,
  state: WorkUrlState,
  parkId: number | null,
): string {
  return `/work/${encodeURIComponent(issueKey)}${buildWorkSearch(state, parkId)}`
}

function scrollKey(userId: number, search: string): string {
  return `robopark.work.scroll.${userId}.${encodeURIComponent(search)}`
}

export function saveWorkScroll(
  userId: number,
  search: string,
  scrollTop: number,
): void {
  try {
    sessionStorage.setItem(
      scrollKey(userId, search),
      String(Math.max(0, Math.round(scrollTop))),
    )
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

export function readWorkScroll(userId: number, search: string): number {
  try {
    const value = Number(sessionStorage.getItem(scrollKey(userId, search)))
    return Number.isFinite(value) && value > 0 ? value : 0
  } catch {
    return 0
  }
}
