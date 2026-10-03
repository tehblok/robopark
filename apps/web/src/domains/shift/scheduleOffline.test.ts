import { describe, expect, it } from 'vitest'
import type { ScheduleEntry } from '../../api'
import type { OfflineAction } from '../../pwa/offlineTypes'
import { hasPendingScheduleCreate, projectScheduleActions } from './scheduleOffline'

const entry: ScheduleEntry = {
  id: 'shift-1', park_id: 1, owner_user_id: 7, kind: 'shift',
  start_at: '2026-09-21T06:00:00Z', end_at: '2026-09-21T18:00:00Z',
  source: 'self', series_id: null, created_by_user_id: 7, updated_by_user_id: 7,
  created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', warnings: [],
}
const action = (id: string, name: string, resourceId: string, payload: Record<string, unknown>, state: OfflineAction['state'] = 'ready'): OfflineAction => ({
  id, deviceId: 'phone', resourceType: 'schedule_entry', resourceId, action: name,
  idempotencyKey: id, baseRevision: entry.updated_at, dependencies: [], payload,
  state, createdAt: 10, updatedAt: 10,
})

describe('projectScheduleActions', () => {
  it('restores a queued create from the device and marks it as pending', () => {
    const queued = action('new-1', 'schedule_create', 'new-1', {
      park_id: 1, kind: 'vacation', start_at: entry.start_at, end_at: entry.end_at,
    })
    const result = projectScheduleActions([], [queued], 1, 7)
    expect(result.items).toEqual([expect.objectContaining({ id: 'local:new-1', kind: 'vacation', owner_user_id: 7 })])
    expect(result.pendingIds.has('local:new-1')).toBe(true)
  })

  it('keeps a queued period visible when an older device record has no valid creation time', () => {
    const queued = {
      ...action('new-1', 'schedule_create', 'new-1', {
        park_id: 1, kind: 'shift', start_at: entry.start_at, end_at: entry.end_at,
      }),
      createdAt: Number.NaN,
      updatedAt: Date.parse('2026-09-20T10:00:00Z'),
    }

    const result = projectScheduleActions([], [queued], 1, 7)

    expect(result.items).toEqual([expect.objectContaining({
      id: 'local:new-1', created_at: '2026-09-20T10:00:00.000Z',
    })])
    expect(result.pendingIds.has('local:new-1')).toBe(true)
  })

  it('overlays pending edits and hides pending deletions, then restores rows on conflict', () => {
    const edit = action('edit-1', 'schedule_update', entry.id, { park_id: 1, kind: 'sick', start_at: entry.start_at, end_at: entry.end_at })
    expect(projectScheduleActions([entry], [edit], 1, 7).items[0].kind).toBe('sick')
    expect(projectScheduleActions([entry], [action('delete-1', 'schedule_delete', entry.id, { park_id: 1 })], 1, 7).items).toEqual([])
    const conflicted = { ...edit, state: 'conflict' as const }
    const result = projectScheduleActions([entry], [conflicted], 1, 7)
    expect(result.items[0].kind).toBe('shift')
    expect(result.attentionIds.has(entry.id)).toBe(true)
  })

  it('uses a confirmed server result until the cached snapshot catches up', () => {
    const confirmed = {
      ...action('edit-1', 'schedule_update', entry.id, { park_id: 1 }, 'confirmed'),
      result: { entry: { ...entry, kind: 'vacation', updated_at: '2026-09-21T11:00:00Z' } },
    }
    expect(projectScheduleActions([entry], [confirmed], 1, 7).items[0].kind).toBe('vacation')
    const newer = { ...entry, kind: 'sick' as const, updated_at: '2026-09-21T12:00:00Z' }
    expect(projectScheduleActions([newer], [confirmed], 1, 7).items[0].kind).toBe('sick')
  })

  it('ignores actions for other parks', () => {
    const other = action('delete-1', 'schedule_delete', entry.id, { park_id: 2 })
    expect(projectScheduleActions([entry], [other], 1, 7).items).toEqual([entry])
  })

  it('keeps cached ranges bounded when queued periods belong to another week', () => {
    const later = action('new-2', 'schedule_create', 'new-2', {
      park_id: 1, kind: 'shift', start_at: '2026-10-21T06:00:00Z', end_at: '2026-10-21T18:00:00Z',
    })
    const range = { start: new Date('2026-09-21T00:00:00Z'), end: new Date('2026-09-28T00:00:00Z') }
    expect(projectScheduleActions([entry], [later], 1, 7, range).items).toEqual([entry])
  })

  it('recognizes the same pending personal period before a second save', () => {
    const queued = action('new-1', 'schedule_create', 'new-1', {
      park_id: 1, owner_user_id: 7, kind: 'shift', start_at: entry.start_at, end_at: entry.end_at,
    })
    expect(hasPendingScheduleCreate([queued], 1, 7, entry)).toBe(true)
    expect(hasPendingScheduleCreate([queued], 1, 8, entry)).toBe(false)
    expect(hasPendingScheduleCreate([{ ...queued, state: 'conflict' }], 1, 7, entry)).toBe(false)
  })

  it('hides an intersecting shift for a queued absence without deleting later cycle days', () => {
    const later = { ...entry, id: 'shift-2', start_at: '2026-09-25T06:00:00Z', end_at: '2026-09-25T18:00:00Z' }
    const queued = action('leave-1', 'schedule_create', 'leave-1', {
      park_id: 1,
      owner_user_id: 7,
      kind: 'sick',
      start_at: '2026-09-21T00:00:00Z',
      end_at: '2026-09-22T00:00:00Z',
    })

    const result = projectScheduleActions([entry, later], [queued], 1, 7)

    expect(result.items.map(item => item.id)).toEqual(['shift-2', 'local:leave-1'])
    expect(result.pendingIds.has('local:leave-1')).toBe(true)
  })
})
