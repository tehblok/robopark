import { describe, expect, it } from 'vitest'
import {
  buildClaimAction,
  buildCommentAction,
  buildHandoffAction,
  buildInventoryWriteoffAction,
  buildSubmitReviewAction,
} from './offlineTaskActions'

const common = { issueKey: 'RP-77', parkId: 7, id: '12345678-action', now: 1_789_000_000_000 }

describe('offline task actions', () => {
  it('keeps the park and stable identity for a queued claim', () => {
    expect(buildClaimAction(common)).toMatchObject({
      id: common.id, idempotencyKey: common.id, action: 'claim', resourceId: common.issueKey,
      payload: { park_id: common.parkId }, dependencies: [],
    })
  })

  it('builds an optimistic comment and keeps one stable replay identity', () => {
    const result = buildCommentAction({ ...common, author: 'Механик', text: 'Заменил колесо' })

    expect(result.action).toMatchObject({
      id: common.id, idempotencyKey: common.id, action: 'comment', resourceId: 'RP-77',
      payload: { text: 'Заменил колесо', park_id: 7 }, dependencies: [],
    })
    expect(result.timelineItem).toMatchObject({
      id: `local:${common.id}`, author: 'Механик', text: 'Заменил колесо', sync_state: 'saved',
    })
  })

  it('rejects an empty comment before it reaches the queue', () => {
    expect(() => buildCommentAction({ ...common, author: 'Механик', text: '   ' })).toThrow('task_comment_required')
  })

  it('encodes handoff and inventory writeoff without lossy quantity conversion', () => {
    expect(buildHandoffAction({ ...common, assignee: 'next', reason: 'Смена', done: 'Диагностика', remaining: 'Колесо', obstacles: '' }).action)
      .toMatchObject({ action: 'handoff', payload: { assignee: 'next', reason: 'Смена', done: 'Диагностика', remaining: 'Колесо', obstacles: '', park_id: 7 } })
    expect(buildInventoryWriteoffAction({ ...common, partId: -900719925, quantity: '9223372036854775807' })).toMatchObject({
      action: 'inventory_writeoff', payload: { part_id: -900719925, quantity: '9223372036854775807', park_id: 7 },
    })
  })

  it('orders review after its media and latest comment dependencies', () => {
    const result = buildSubmitReviewAction({ ...common, defectCode: 'BD-01', mediaActionId: 'media-12345678', commentActionId: 'comment-12345678' })
    expect(result).toMatchObject({
      action: 'submit_review', dependencies: ['media-12345678', 'comment-12345678'],
      payload: { defect_code: 'BD-01', media_id: 'media-12345678', park_id: 7 },
    })
  })
})
