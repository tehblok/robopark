import { webcrypto } from 'node:crypto'
import { beforeEach, expect, it, vi } from 'vitest'
import { runTrackerSubmission } from './trackerReliability'

beforeEach(() => localStorage.clear())
const issue = { key: 'ROBOPARK-1', status: 'Открыта', status_key: 'open', assignee: null }

it('does not persist a lost response or block a deliberate retry', async () => {
  const sent: Record<string, string>[] = []
  const lost = vi.fn(async (headers: Record<string, string>) => { sent.push(headers); throw new Error('lost') })
  await expect(runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, lost)).rejects.toThrow('lost')
  await expect(runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, lost)).rejects.toThrow('lost')
  expect(sent[1]['Idempotency-Key']).not.toBe(sent[0]['Idempotency-Key'])
  expect(JSON.parse(decodeURIComponent(sent[0]['X-Tracker-State']))).toEqual({ status: 'Открыта', status_key: 'open', assignee: '' })
  await expect(runTrackerSubmission('bob', issue, 'comment', { text: 'done' }, lost)).rejects.toThrow('lost')
  expect(sent[2]['Idempotency-Key']).not.toBe(sent[0]['Idempotency-Key'])
})

it('a successful acknowledged submission allows another intentional identical comment', async () => {
  const sent: string[] = []
  const write = async (headers: Record<string, string>) => { sent.push(headers['Idempotency-Key']) }
  await runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, write)
  await runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, write)
  expect(sent[1]).not.toBe(sent[0])
})

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })

it('stores no pending submission data in local storage', async () => {
  const secretText = 'private shift detail'
  await expect(runTrackerSubmission('alice', issue, 'comment', { text: secretText }, async () => { throw new Error('lost') })).rejects.toThrow()
  expect(localStorage.length).toBe(0)
})

it.each([403, 409])('uses a fresh key after a %s denial or busy task', async status => {
  const { ApiError } = await import('../../api')
  const sent: string[] = []
  const send = async (headers: Record<string, string>) => {
    sent.push(headers['Idempotency-Key'])
    throw new ApiError(status, status === 409 ? 'tracker_task_busy' : null)
  }
  await expect(runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, send)).rejects.toThrow()
  await expect(runTrackerSubmission('alice', issue, 'comment', { text: 'done' }, send)).rejects.toThrow()
  expect(sent[1]).not.toBe(sent[0])
})
