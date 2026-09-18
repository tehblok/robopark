import { afterEach, describe, expect, it, vi } from 'vitest'
import qrcode from 'qrcode-generator'
import { createQrDetector } from './robotScannerFallback'

describe('robot scanner QR fallback', () => {
  afterEach(() => vi.restoreAllMocks())

  it('decodes a camera frame containing a robot VIN', async () => {
    const vin = 'YASADR00000001975'
    const qr = qrcode(0, 'M')
    qr.addData(vin)
    qr.make()
    const scale = 8
    const margin = 4
    const size = (qr.getModuleCount() + margin * 2) * scale
    const data = new Uint8ClampedArray(size * size * 4)
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const row = Math.floor(y / scale) - margin
        const col = Math.floor(x / scale) - margin
        const dark = row >= 0 && col >= 0 && row < qr.getModuleCount() && col < qr.getModuleCount() && qr.isDark(row, col)
        const offset = (y * size + x) * 4
        data[offset] = data[offset + 1] = data[offset + 2] = dark ? 0 : 255
        data[offset + 3] = 255
      }
    }
    const getImageData = vi.fn(() => ({ data, width: size, height: size }))
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn(), getImageData } as unknown as CanvasRenderingContext2D)
    const video = document.createElement('video')
    Object.defineProperty(video, 'videoWidth', { value: size })
    Object.defineProperty(video, 'videoHeight', { value: size })

    await expect(createQrDetector().detect(video)).resolves.toEqual([{ rawValue: vin }])
  })

  it('bounds large camera frames before local QR decoding', async () => {
    const getImageData = vi.fn(() => ({ data: new Uint8ClampedArray(640 * 360 * 4), width: 640, height: 360 }))
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn(), getImageData } as unknown as CanvasRenderingContext2D)
    const video = document.createElement('video')
    Object.defineProperty(video, 'videoWidth', { value: 1920 })
    Object.defineProperty(video, 'videoHeight', { value: 1080 })

    await createQrDetector().detect(video)
    expect(getImageData).toHaveBeenCalledWith(0, 0, 640, 360)
  })
})
