import jsQR from 'jsqr'
import type { BarcodeDetectorLike } from './RobotScanner'

// Loaded only after the user opens the scanner on browsers without BarcodeDetector.
export function createQrDetector(): BarcodeDetectorLike {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) throw new Error('Canvas is unavailable')

  return {
    async detect(source) {
      if (!(source instanceof HTMLVideoElement) || !source.videoWidth || !source.videoHeight) return []
      const scale = Math.min(1, 640 / Math.max(source.videoWidth, source.videoHeight))
      canvas.width = Math.max(1, Math.round(source.videoWidth * scale))
      canvas.height = Math.max(1, Math.round(source.videoHeight * scale))
      context.drawImage(source, 0, 0, canvas.width, canvas.height)
      const image = context.getImageData(0, 0, canvas.width, canvas.height)
      const code = jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' })
      return code ? [{ rawValue: code.data }] : []
    },
  }
}
