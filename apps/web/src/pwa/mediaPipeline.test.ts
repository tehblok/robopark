import { describe, expect, it, vi } from 'vitest'
import { prepareImage } from './mediaPipeline'

describe('prepareImage', () => {
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
