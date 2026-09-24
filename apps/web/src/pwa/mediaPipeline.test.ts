import { afterEach, describe, expect, it, vi } from 'vitest'
import { prepareImage } from './mediaPipeline'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('prepareImage', () => {
  it('requests a 1920px WebP conversion at 82% quality', async () => {
    const postMessage = vi.fn()
    const terminate = vi.fn()
    class WorkerStub {
      onmessage?: (event: MessageEvent<{ id: string, blob?: Blob }>) => void
      onerror?: () => void
      postMessage = (request: { id: string, maxEdge: number, quality: number }) => {
        postMessage(request)
        this.onmessage?.({ data: { id: request.id, blob: new Blob(['webp'], { type: 'image/webp' }) } } as MessageEvent)
      }
      terminate = terminate
    }
    vi.stubGlobal('Worker', WorkerStub)
    vi.stubGlobal('createImageBitmap', vi.fn())
    vi.stubGlobal('OffscreenCanvas', class {})
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')

    await prepareImage(new File(['photo'], 'robot.jpg', { type: 'image/jpeg' }))

    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ maxEdge: 1920, quality: 0.82 }))
    expect(terminate).toHaveBeenCalledOnce()
  })

  it.each(['qr', 'document'] as const)('keeps the original bytes for %s media', async kind => {
    const Worker = vi.fn()
    vi.stubGlobal('Worker', Worker)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')
    const source = new File(['original'], `${kind}.png`, { type: 'image/png' })

    const prepared = await prepareImage(source, { kind })

    expect(prepared.blob).toBe(source)
    expect(Worker).not.toHaveBeenCalled()
  })

  it('keeps the original when worker conversion fails', async () => {
    class WorkerStub {
      onmessage?: (event: MessageEvent<{ id: string, error?: string }>) => void
      onerror?: () => void
      postMessage(request: { id: string }) { this.onmessage?.({ data: { id: request.id, error: 'decode failed' } } as MessageEvent) }
      terminate() {}
    }
    vi.stubGlobal('Worker', WorkerStub)
    vi.stubGlobal('createImageBitmap', vi.fn())
    vi.stubGlobal('OffscreenCanvas', class {})
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')
    const source = new File(['original'], 'robot.jpg', { type: 'image/jpeg' })

    const prepared = await prepareImage(source)

    expect(prepared.blob).toBe(source)
  })

  it('produces a stable checksum and revocable preview for accepted images', async () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const source = new File(['same-image'], 'robot.jpg', { type: 'image/jpeg' })

    const first = await prepareImage(source)
    const second = await prepareImage(source)

    expect(first.sha256).toBe(second.sha256)
    expect(first.previewUrl).toBe('blob:preview')
    first.releasePreview()
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })

  it('rejects an oversized source before allocating a preview', async () => {
    const source = new File([new Uint8Array(16)], 'robot.jpg', { type: 'image/jpeg' })
    await expect(prepareImage(source, { maxSourceBytes: 8 })).rejects.toThrow('media_too_large')
  })

  it('rejects unsupported content types', async () => {
    await expect(prepareImage(new File(['x'], 'robot.svg', { type: 'image/svg+xml' }))).rejects.toThrow('media_invalid_type')
  })
})
