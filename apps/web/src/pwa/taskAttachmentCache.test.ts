import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { TrackerAttachment } from '../api'
import {
  activateTaskAttachmentCache,
  clearTaskAttachmentCache,
  loadTaskAttachment,
  releaseTaskAttachment,
} from './taskAttachmentCache'

const attachment = (id: number): TrackerAttachment => ({
  id: String(id),
  name: `repair-${id}.jpg`,
  url: `/api/tracker/issues/RP-1/attachments/${id}/content`,
  mimetype: 'image/jpeg',
})

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  class TestURL extends URL {}
  TestURL.createObjectURL = vi.fn((blob: Blob) => `blob:${blob.size}:${Math.random()}`)
  TestURL.revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', TestURL)
})

afterEach(async () => {
  await clearTaskAttachmentCache()
  vi.unstubAllGlobals()
})

it('evicts the least recently used attachment when a 65th entry is cached', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async (url: string) => new Blob([url], { type: 'image/jpeg' }))

  for (let id = 0; id < 65; id += 1) {
    releaseTaskAttachment(await loadTaskAttachment(attachment(id), fetcher))
  }
  await loadTaskAttachment(attachment(0), fetcher)

  expect(fetcher).toHaveBeenCalledTimes(66)
})

it('keeps the attachment cache below 32 MiB', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob([new Uint8Array(1024 * 1024)], { type: 'image/jpeg' }))

  for (let id = 0; id < 33; id += 1) {
    releaseTaskAttachment(await loadTaskAttachment(attachment(id), fetcher))
  }
  await loadTaskAttachment(attachment(0), fetcher)

  expect(fetcher).toHaveBeenCalledTimes(34)
})

it('clears the active user namespace on logout', async () => {
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher))

  await clearTaskAttachmentCache()
  await activateTaskAttachmentCache(7)
  await loadTaskAttachment(attachment(1), fetcher)

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('purges the previous namespace across a direct 7 to 8 to 7 user switch', async () => {
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher))

  await activateTaskAttachmentCache(8)
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher))

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('does not return or retain a blob when the user switches during persistence', async () => {
  let resolve!: (blob: Blob) => void
  const response = new Promise<Blob>(done => { resolve = done })
  const fetcher = vi.fn(() => response)
  await activateTaskAttachmentCache(7)
  const loading = loadTaskAttachment(attachment(1), fetcher)
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const rejected = expect(loading).rejects.toThrow('task_attachment_session_changed')

  resolve(new Blob(['private'], { type: 'image/jpeg' }))
  await Promise.resolve()
  await Promise.resolve()
  await activateTaskAttachmentCache(8)

  await rejected
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), async () => new Blob(['fresh'], { type: 'image/jpeg' })))
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
})

it('does not persist a successful response whose body is not an image', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['html'], { type: 'text/html' }))

  await expect(loadTaskAttachment(attachment(1), fetcher)).rejects.toThrow('task_attachment_not_image')
  await expect(loadTaskAttachment(attachment(1), fetcher)).rejects.toThrow('task_attachment_not_image')

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('discards a pending attachment response after logout', async () => {
  let resolve!: (blob: Blob) => void
  const response = new Promise<Blob>(done => { resolve = done })
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(() => response)
  const loading = loadTaskAttachment(attachment(1), fetcher)
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const rejected = expect(loading).rejects.toThrow('task_attachment_session_changed')

  await clearTaskAttachmentCache()
  resolve(new Blob(['private'], { type: 'image/jpeg' }))

  await rejected
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('revokes only the object URL owned by the caller', () => {
  releaseTaskAttachment('blob:repair')
  releaseTaskAttachment('https://tracker.example/repair.jpg')

  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:repair')
})
