import { repairFieldsPayload, type TaskRepairFields, type TaskTimelineItem } from '../../api'
import type { OfflineActionInput } from '../../pwa/syncEngine'
import type { OfflineAction } from '../../pwa/offlineTypes'

type ActionBase = {
  issueKey: string
  parkId: number
  id: string
  now?: number
}

function action(base: ActionBase, name: string, payload: Record<string, unknown>, dependencies: string[] = []): OfflineActionInput {
  return {
    id: base.id,
    deviceId: 'local',
    resourceType: 'tracker_issue',
    resourceId: base.issueKey,
    action: name,
    idempotencyKey: base.id,
    baseRevision: null,
    dependencies,
    payload: { ...payload, park_id: base.parkId },
  }
}

export function buildClaimAction(base: ActionBase & { componentIds?: string[] }): OfflineActionInput {
  return action(base, 'claim', base.componentIds ? { component_ids: base.componentIds } : {})
}

export function buildCommentAction(base: ActionBase & { author: string, text: string }): {
  action: OfflineActionInput
  timelineItem: TaskTimelineItem
} {
  const text = base.text.trim()
  if (!text) throw new Error('task_comment_required')
  const createdAt = new Date(base.now ?? Date.now()).toISOString()
  return {
    action: action(base, 'comment', { text }),
    timelineItem: {
      id: `local:${base.id}`,
      kind: 'user',
      author: base.author,
      text,
      created_at: createdAt,
      sync_state: 'saved',
      attachments: [],
    },
  }
}

export function buildHandoffAction(base: ActionBase & {
  assignee: string
  reason: string
  done?: string
  remaining?: string
  obstacles?: string
}): { action: OfflineActionInput, timelineItem: TaskTimelineItem } {
  const payload = {
    assignee: base.assignee.trim(),
    reason: base.reason.trim(),
    done: base.done?.trim() ?? '',
    remaining: base.remaining?.trim() ?? '',
    obstacles: base.obstacles?.trim() ?? '',
  }
  if (!payload.assignee || !payload.reason) throw new Error('task_handoff_required')
  return {
    action: action(base, 'handoff', payload),
    timelineItem: {
      id: `local:${base.id}`,
      kind: 'system',
      author: 'Бот',
      text: `Передача смены: ${payload.reason}`,
      created_at: new Date(base.now ?? Date.now()).toISOString(),
      sync_state: 'saved',
      attachments: [],
    },
  }
}

// The durable command is the source of truth after a cold start; the cached
// timeline may predate an optimistic update or have been evicted independently.
export function mergeQueuedTaskTimeline(items: TaskTimelineItem[], actions: OfflineAction[],
  issueKey: string, parkId: number, author: string): TaskTimelineItem[] {
  const relevant = actions.filter(item => item.resourceType === 'tracker_issue' && item.resourceId === issueKey
    && item.payload && typeof item.payload === 'object'
    && (item.payload as Record<string, unknown>).park_id === parkId
    && ['comment', 'handoff'].includes(item.action))
    .sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id))
  const ids = new Set(relevant.map(item => `local:${item.id}`))
  const result = items.filter(item => !ids.has(item.id))
  for (const item of relevant) {
    if (!['local', 'ready', 'sending'].includes(item.state)) continue
    const payload = item.payload as Record<string, unknown>
    const base = { issueKey, parkId, id: item.id, now: item.createdAt }
    if (item.action === 'comment' && typeof payload.text === 'string' && payload.text.trim()) {
      result.push(buildCommentAction({ ...base, author, text: payload.text }).timelineItem)
    } else if (item.action === 'handoff' && typeof payload.assignee === 'string' && payload.assignee.trim()
      && typeof payload.reason === 'string' && payload.reason.trim()) {
      result.push(buildHandoffAction({ ...base, assignee: payload.assignee, reason: payload.reason }).timelineItem)
    }
  }
  return result
}

export function buildInventoryWriteoffAction(base: ActionBase & { partId: number, quantity: string }): OfflineActionInput {
  return action(base, 'inventory_writeoff', { part_id: base.partId, quantity: base.quantity })
}

export function buildSubmitReviewAction(base: ActionBase & {
  defectCode: string
  mediaActionId: string
  repairFields?: TaskRepairFields
  comment?: string
}): OfflineActionInput {
  return action(base, 'submit_review', {
    defect_code: base.defectCode,
    media_id: base.mediaActionId,
    ...(base.comment?.trim() ? { comment: base.comment.trim() } : {}),
    ...(base.repairFields ? { repair_fields: repairFieldsPayload(base.repairFields) } : {}),
  }, [base.mediaActionId])
}
