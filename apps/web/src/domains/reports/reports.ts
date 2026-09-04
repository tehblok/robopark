import { api, type Park, type User } from '../../api'
import { REPORT_DRAFT_STORAGE_PREFIX } from '../../shared/auth/protectedBrowserStorage'

export type ReportsApiClient = Pick<typeof api,
  | 'createReport'
  | 'report'
  | 'reportAttach'
  | 'reportAttachmentUrl'
  | 'reportDone'
  | 'reportEscalate'
  | 'reportReturn'
  | 'reportsBadge'
  | 'reportsInbox'
  | 'reportsMine'
>

function parkIdentity(park: Park) {
  return [
    park.id,
    park.name.trim(),
    park.tag.trim(),
    park.tracker_queue?.trim() ?? null,
    park.is_active !== false,
  ]
}

export function reportsAccessIdentity(user: User, selectedPark?: Park | null): string {
  return JSON.stringify([
    user.id,
    user.username,
    user.tracker_login ?? null,
    user.role,
    user.access_status,
    Boolean(user.must_change_password),
    [...new Set(user.permissions ?? [])].sort(),
    [...user.parks].sort((left, right) => left.id - right.id).map(parkIdentity),
    selectedPark ? parkIdentity(selectedPark) : null,
  ])
}

export function reportDraftKey(principalId: number, parkId: number): string {
  return `${REPORT_DRAFT_STORAGE_PREFIX}${principalId}:${parkId}`
}
