import { describe, expect, it, vi } from 'vitest'
import { uploadMedia } from './resumableUpload'

describe('uploadMedia', () => {
  it('continues at the server offset and completes with the same media identity', async () => {
    const blob = new Blob(['abcdefghij'], { type: 'image/jpeg' })
    const create = vi.fn(async () => ({ upload_id: 'upload-1', received_offset: 4, completed: false }))
    const putChunk = vi.fn(async (_id: string, offset: number, chunk: Blob) => ({ received_offset: offset + chunk.size }))
    const complete = vi.fn(async () => ({ upload_id: 'upload-1', media_id: 'media-1', completed: true as const }))

    const result = await uploadMedia({ id: 'media-1', actionId: 'review-1', deviceId: 'account-7', blob, mimeType: blob.type, sha256: 'hash', name: 'robot.jpg' }, { create, putChunk, complete }, { chunkBytes: 3 })

    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      media_id: 'media-1', dependent_action_id: 'review-1', device_id: 'account-7',
    }))
    expect(putChunk.mock.calls.map(call => [call[1], call[2].size])).toEqual([[4, 3], [7, 3]])
    expect(result.media_id).toBe('media-1')
  })

  it('does not finalize after an interrupted chunk', async () => {
    const complete = vi.fn()
    await expect(uploadMedia(
      { id: 'media-2', actionId: 'review-2', deviceId: 'account-7', blob: new Blob(['abc']), mimeType: 'image/jpeg', sha256: 'hash', name: 'robot.jpg' },
      {
        create: vi.fn(async () => ({ upload_id: 'upload-2', received_offset: 0, completed: false })),
        putChunk: vi.fn(async () => { throw new TypeError('offline') }),
        complete,
      },
    )).rejects.toThrow('offline')
    expect(complete).not.toHaveBeenCalled()
  })
})
