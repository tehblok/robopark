import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, clearApiValidators } from './api'
import { preparePhotoFile } from './pwa/mediaPipeline'
vi.mock('./pwa/mediaPipeline', () => ({ preparePhotoFile: vi.fn() }))
afterEach(() => { clearApiValidators(); vi.unstubAllGlobals(); vi.mocked(preparePhotoFile).mockReset() })

describe('photo API boundaries', () => {
  it.each(['photo', 'review'])('prepares %s once and preserves the mutation ID', async kind => {
    const original = new File(['original'], 'photo.png', { type: 'image/png' })
    const small = new File(['small'], 'photo.webp', { type: 'image/webp' })
    vi.mocked(preparePhotoFile).mockResolvedValue(small)
    const fetcher = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetcher)
    if (kind === 'photo') await api.taskPhoto('TEST-1', original, 'stable-action', 12)
    else await api.taskSubmitReview('TEST-1', { photo: original, defectCode: 'wheel', comment: ' done ' }, 'stable-action')
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect((init.body as FormData).get(kind === 'photo' ? 'file' : 'photo')).toMatchObject({ name: 'photo.webp', size: 5, type: 'image/webp' })
    expect(init.headers).toMatchObject({ 'Idempotency-Key': 'stable-action' })
    expect(preparePhotoFile).toHaveBeenCalledOnce()
  })
  it.each(['photo', 'review'])('never sends %s under a new account after asynchronous processing', async kind => {
    const file = new File(['image'], 'photo.png', { type: 'image/png' })
    vi.mocked(preparePhotoFile).mockImplementation(async () => { clearApiValidators(); return file })
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const sending = kind === 'photo' ? api.taskPhoto('TEST-1', file, 'stable-action') : api.taskSubmitReview('TEST-1', { photo: file, defectCode: 'wheel' }, 'stable-action')
    await expect(sending).rejects.toMatchObject({ status: 409, detail: 'account_changed' })
    expect(fetcher).not.toHaveBeenCalled()
  })
})
