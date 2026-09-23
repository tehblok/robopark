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

const authorize = vi.fn(async (_url: string) => undefined)

beforeEach(() => {
  authorize.mockClear()
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
    releaseTaskAttachment(await loadTaskAttachment(attachment(id), fetcher, authorize))
  }
  await loadTaskAttachment(attachment(0), fetcher, authorize)

  expect(fetcher).toHaveBeenCalledTimes(66)
})

it('keeps the attachment cache below 32 MiB', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob([new Uint8Array(1024 * 1024)], { type: 'image/jpeg' }))

  for (let id = 0; id < 33; id += 1) {
    releaseTaskAttachment(await loadTaskAttachment(attachment(id), fetcher, authorize))
  }
  await loadTaskAttachment(attachment(0), fetcher, authorize)

  expect(fetcher).toHaveBeenCalledTimes(34)
})

it('clears the active user namespace on logout', async () => {
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  await clearTaskAttachmentCache()
  await activateTaskAttachmentCache(7)
  await loadTaskAttachment(attachment(1), fetcher, authorize)

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('purges the previous namespace across a direct 7 to 8 to 7 user switch', async () => {
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  await activateTaskAttachmentCache(8)
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('does not return or retain a blob when the user switches during persistence', async () => {
  let resolve!: (blob: Blob) => void
  const response = new Promise<Blob>(done => { resolve = done })
  const fetcher = vi.fn(() => response)
  await activateTaskAttachmentCache(7)
  const loading = loadTaskAttachment(attachment(1), fetcher, authorize)
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const rejected = expect(loading).rejects.toThrow('task_attachment_session_changed')

  resolve(new Blob(['private'], { type: 'image/jpeg' }))
  await Promise.resolve()
  await Promise.resolve()
  await activateTaskAttachmentCache(8)

  await rejected
  await activateTaskAttachmentCache(7)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), async () => new Blob(['fresh'], { type: 'image/jpeg' }), authorize))
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
})

it('does not persist a successful response whose body is not an image', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['html'], { type: 'text/html' }))

  await expect(loadTaskAttachment(attachment(1), fetcher, authorize)).rejects.toThrow('task_attachment_not_image')
  await expect(loadTaskAttachment(attachment(1), fetcher, authorize)).rejects.toThrow('task_attachment_not_image')

  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('discards a pending attachment response after logout', async () => {
  let resolve!: (blob: Blob) => void
  const response = new Promise<Blob>(done => { resolve = done })
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(() => response)
  const loading = loadTaskAttachment(attachment(1), fetcher, authorize)
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

it('reauthorizes a cache hit without downloading the blob again', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  expect(authorize).toHaveBeenCalledWith(attachment(1).url)
  expect(fetcher).toHaveBeenCalledOnce()
})

it('evicts a denied cache hit and never returns its object URL', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))
  authorize.mockRejectedValueOnce(new Error('forbidden'))

  await expect(loadTaskAttachment(attachment(1), fetcher, authorize)).rejects.toThrow('forbidden')
  authorize.mockResolvedValue(undefined)
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
})

it('evicts an expired cache entry before loading a fresh blob', async () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(2_000_000_000_000)
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))
  now.mockReturnValue(2_000_000_000_000 + 5 * 60 * 1000 + 1)

  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))

  expect(authorize).not.toHaveBeenCalled()
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('does not recache or expose an expired attachment when the authorized GET is denied', async () => {
  const now = vi.spyOn(Date, 'now').mockReturnValue(2_000_000_000_000)
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))
  now.mockReturnValue(2_000_000_000_000 + 5 * 60 * 1000 + 1)
  fetcher.mockRejectedValue(new Error('task_already_closed'))

  await expect(loadTaskAttachment(attachment(1), fetcher, authorize)).rejects.toThrow('task_already_closed')
  await expect(loadTaskAttachment(attachment(1), fetcher, authorize)).rejects.toThrow('task_already_closed')

  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
  expect(authorize).not.toHaveBeenCalled()
})

it('does not expose a cache hit when logout races its reauthorization', async () => {
  await activateTaskAttachmentCache(7)
  const fetcher = vi.fn(async () => new Blob(['photo'], { type: 'image/jpeg' }))
  releaseTaskAttachment(await loadTaskAttachment(attachment(1), fetcher, authorize))
  let finishAuthorization!: () => void
  const waitingAuthorization = new Promise<void>(resolve => { finishAuthorization = resolve })
  authorize.mockImplementationOnce(() => waitingAuthorization.then(() => undefined))
  const loading = loadTaskAttachment(attachment(1), fetcher, authorize)
  await vi.waitFor(() => expect(authorize).toHaveBeenCalledOnce())

  await clearTaskAttachmentCache()
  finishAuthorization()

  await expect(loading).rejects.toThrow('task_attachment_session_changed')
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
})
