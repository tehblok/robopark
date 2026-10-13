import type { Park, User } from '../../api'
import type { WorkUrlState } from './workUrl'

const parkScope = (park: Park) => [park.id, park.tag?.trim(), park.tracker_queue?.trim(), park.is_active !== false]

export function workPrincipalKey(user: User): string {
  return JSON.stringify([
    user.id, user.username, user.tracker_login, user.role, user.access_status,
    Boolean(user.must_change_password), [...new Set(user.permissions ?? [])].sort(),
    [...user.parks].sort((a, b) => a.id - b.id).map(parkScope),
  ])
}

export function workAccessKey(user: User, selectedPark: Park): string {
  return `${workPrincipalKey(user)}:${JSON.stringify(parkScope(selectedPark))}`
}

export function workListCacheKey(user: User, park: Park, state: WorkUrlState): string {
  return `work:${user.id}:${workAccessKey(user, park)}:list:${park.id}:${JSON.stringify({ filters: state.filters, sort: state.sort, page: state.page })}`
}
