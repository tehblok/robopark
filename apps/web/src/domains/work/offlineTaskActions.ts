import type { TaskTimelineItem } from '../../api'
import type { OfflineActionInput } from '../../pwa/syncEngine'

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

export function buildInventoryWriteoffAction(base: ActionBase & { partId: number, quantity: string }): OfflineActionInput {
  return action(base, 'inventory_writeoff', { part_id: base.partId, quantity: base.quantity })
}

export function buildSubmitReviewAction(base: ActionBase & {
  defectCode: string
  mediaActionId: string
  commentActionId?: string | null
}): OfflineActionInput {
  const dependencies = [base.mediaActionId, base.commentActionId].filter((value): value is string => Boolean(value))
  return action(base, 'submit_review', {
    defect_code: base.defectCode,
    media_action_id: base.mediaActionId,
  }, dependencies)
}
