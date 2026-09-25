import { describe, expect, it, vi } from 'vitest'
import { uploadMedia } from './resumableUpload'

describe('uploadMedia', () => {
  it('continues at the server offset and completes with the same media identity', async () => {
    const blob = new Blob(['abcdefghij'], { type: 'image/jpeg' })
    const create = vi.fn(async () => ({ upload_id: 'upload-1', received_offset: 4, completed: false, status: 'active' as const }))
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
        create: vi.fn(async () => ({ upload_id: 'upload-2', received_offset: 0, completed: false, status: 'active' as const })),
        putChunk: vi.fn(async () => { throw new TypeError('offline') }),
        complete,
      },
    )).rejects.toThrow('offline')
    expect(complete).not.toHaveBeenCalled()
  })

  it('uploads again when the server explicitly reinitializes a stale completed row', async () => {
    const blob = new Blob(['abcdef'], { type: 'image/jpeg' })
    const putChunk = vi.fn(async (_id: string, offset: number, chunk: Blob) => ({ received_offset: offset + chunk.size }))
    const complete = vi.fn(async () => ({ upload_id: 'upload-stale', media_id: 'media-stale', completed: true as const }))

    await uploadMedia(
      { id: 'media-stale', actionId: 'review-stale', deviceId: 'account-7', blob, mimeType: blob.type, sha256: 'hash', name: 'robot.jpg' },
      {
        create: vi.fn(async () => ({
          upload_id: 'upload-stale', received_offset: 0, completed: true,
          media_id: 'media-stale', status: 'reinitialized' as const,
        })),
        putChunk,
        complete,
      },
      { chunkBytes: 3 },
    )

    expect(putChunk).toHaveBeenCalledTimes(2)
    expect(complete).toHaveBeenCalledWith('upload-stale')
  })

  it('skips an intact upload only when the server reports completed status', async () => {
    const blob = new Blob(['abcdef'], { type: 'image/jpeg' })
    const putChunk = vi.fn()
    const complete = vi.fn()

    const result = await uploadMedia(
      { id: 'media-intact', actionId: 'review-intact', deviceId: 'account-7', blob, mimeType: blob.type, sha256: 'hash', name: 'robot.jpg' },
      {
        create: vi.fn(async () => ({
          upload_id: 'upload-intact', received_offset: blob.size, completed: true,
          media_id: 'media-intact', status: 'completed' as const,
        })),
        putChunk,
        complete,
      },
    )

    expect(result).toEqual({ upload_id: 'upload-intact', media_id: 'media-intact', completed: true })
    expect(putChunk).not.toHaveBeenCalled()
    expect(complete).not.toHaveBeenCalled()
  })
})
