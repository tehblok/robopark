import jsQR from 'jsqr'
import type { BarcodeDetectorLike } from './RobotScanner'

// Loaded only after the user opens the scanner on browsers without BarcodeDetector.
export function createQrDetector(): BarcodeDetectorLike {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('Canvas is unavailable')

  return {
    async detect(source) {
      if (source instanceof Blob || (typeof ImageData !== 'undefined' && source instanceof ImageData)) return []
      const drawable = source as CanvasImageSource
      const dimensions = source instanceof HTMLVideoElement
        ? { width: source.videoWidth, height: source.videoHeight }
        : { width: 'width' in source ? Number(source.width) : 0, height: 'height' in source ? Number(source.height) : 0 }
      if (!dimensions.width || !dimensions.height) return []
      const scale = Math.min(1, 640 / Math.max(dimensions.width, dimensions.height))
      canvas.width = Math.max(1, Math.round(dimensions.width * scale))
      canvas.height = Math.max(1, Math.round(dimensions.height * scale))
      context.drawImage(drawable, 0, 0, canvas.width, canvas.height)
      const image = context.getImageData(0, 0, canvas.width, canvas.height)
      const code = jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' })
      return code ? [{ rawValue: code.data }] : []
    },
  }
}
