import type { ScheduleEntry } from '../../api'
import type { OfflineAction } from '../../pwa/offlineTypes'

type ScheduleProjection = {
  items: ScheduleEntry[]
  pendingIds: Set<string>
  attentionIds: Set<string>
}

const pendingStates = new Set<OfflineAction['state']>(['local', 'ready', 'sending'])

export function hasPendingScheduleCreate(
  actions: OfflineAction[], parkId: number, userId: number,
  period: Pick<ScheduleEntry, 'kind' | 'start_at' | 'end_at'>,
): boolean {
  return actions.some(action => {
    if (action.resourceType !== 'schedule_entry' || action.action !== 'schedule_create'
      || !pendingStates.has(action.state) || !action.payload || typeof action.payload !== 'object') return false
    const payload = action.payload as Record<string, unknown>
    const ownerId = typeof payload.owner_user_id === 'number' ? payload.owner_user_id : userId
    return payload.park_id === parkId && ownerId === userId && payload.kind === period.kind
      && payload.start_at === period.start_at && payload.end_at === period.end_at
  })
}

function resultEntry(action: OfflineAction, parkId: number): ScheduleEntry | null {
  const raw = action.result?.entry
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const value = raw as Partial<ScheduleEntry>
  if (typeof value.id !== 'string' || value.park_id !== parkId || typeof value.owner_user_id !== 'number'
    || !['shift', 'vacation', 'sick'].includes(value.kind ?? '')
    || typeof value.start_at !== 'string' || typeof value.end_at !== 'string'
    || typeof value.updated_at !== 'string' || !Array.isArray(value.warnings)) return null
  return value as ScheduleEntry
}

function pendingCreate(action: OfflineAction, parkId: number, userId: number): ScheduleEntry | null {
  if (!action.payload || typeof action.payload !== 'object') return null
  const payload = action.payload as Record<string, unknown>
  if (payload.park_id !== parkId || !['shift', 'vacation', 'sick'].includes(String(payload.kind))
    || typeof payload.start_at !== 'string' || typeof payload.end_at !== 'string') return null
  const recordedAt = Number.isFinite(action.createdAt) ? action.createdAt
    : Number.isFinite(action.updatedAt) ? action.updatedAt : Date.parse(payload.start_at)
  const createdDate = new Date(recordedAt)
  if (!Number.isFinite(createdDate.getTime())) return null
  const createdAt = createdDate.toISOString()
  return {
    id: `local:${action.id}`, park_id: parkId,
    owner_user_id: typeof payload.owner_user_id === 'number' ? payload.owner_user_id : userId,
    kind: payload.kind as ScheduleEntry['kind'], start_at: payload.start_at, end_at: payload.end_at,
    source: 'self', series_id: null, created_by_user_id: userId, updated_by_user_id: userId,
    created_at: createdAt, updated_at: createdAt, warnings: [],
  }
}

export function projectScheduleActions(base: ScheduleEntry[], actions: OfflineAction[], parkId: number, userId: number, range?: { start: Date; end: Date }): ScheduleProjection {
  const items = new Map(base.filter(item => item.park_id === parkId).map(item => [item.id, item]))
  const pendingIds = new Set<string>()
  const attentionIds = new Set<string>()
  for (const action of [...actions].sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id))) {
    if (action.resourceType !== 'schedule_entry' || !action.payload || typeof action.payload !== 'object'
      || (action.payload as Record<string, unknown>).park_id !== parkId || action.state === 'cancelled') continue
    const pending = pendingStates.has(action.state)
    if (action.state === 'conflict' || action.state === 'attention') {
      attentionIds.add(action.resourceId)
      continue
    }
    if (action.action === 'schedule_create') {
      const row = pending ? pendingCreate(action, parkId, userId) : resultEntry(action, parkId)
      if (row) {
        const existing = items.get(row.id)
        if (!existing || pending || Date.parse(row.updated_at) >= Date.parse(existing.updated_at)) items.set(row.id, row)
        if (pending) pendingIds.add(row.id)
      }
    } else if (action.action === 'schedule_update') {
      const existing = items.get(action.resourceId)
      if (pending) {
        if (!existing) continue
        const payload = action.payload as Record<string, unknown>
        if (!['shift', 'vacation', 'sick'].includes(String(payload.kind))
          || typeof payload.start_at !== 'string' || typeof payload.end_at !== 'string') continue
        items.set(existing.id, { ...existing, kind: payload.kind as ScheduleEntry['kind'], start_at: payload.start_at, end_at: payload.end_at })
        pendingIds.add(existing.id)
      } else {
        const row = resultEntry(action, parkId)
        if (row && (!existing || Date.parse(row.updated_at) >= Date.parse(existing.updated_at))) items.set(row.id, row)
      }
    } else if (action.action === 'schedule_delete') {
      if (pending || action.state === 'confirmed') items.delete(action.resourceId)
    }
  }
  const projected = [...items.values()]
  const absences = projected.filter(item => item.kind !== 'shift')
  return {
    items: projected.filter(item => {
      if (range && (Date.parse(item.start_at) >= range.end.getTime() || Date.parse(item.end_at) <= range.start.getTime())) return false
      if (item.kind !== 'shift') return true
      return !absences.some(absence => absence.owner_user_id === item.owner_user_id
        && absence.park_id === item.park_id
        && Date.parse(absence.start_at) < Date.parse(item.end_at)
        && Date.parse(absence.end_at) > Date.parse(item.start_at))
    }),
    pendingIds, attentionIds,
  }
}
