import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { BottomSheet } from '../../design-system/overlays/BottomSheet'

export type BarcodeDetectorLike = {
  detect(source: ImageBitmapSource): Promise<Array<{ rawValue?: string }>>
}

export type BarcodeDetectorConstructor = new (options?: { formats?: string[] }) => BarcodeDetectorLike

type DetectorWindow = Window & { BarcodeDetector?: BarcodeDetectorConstructor }

export type RobotScannerProps = {
  open: boolean
  onCancel: () => void
  onDetected: (value: string) => void
  mediaDevices?: Pick<MediaDevices, 'getUserMedia'>
  Detector?: BarcodeDetectorConstructor
}

function browserDetector(): BarcodeDetectorConstructor | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as DetectorWindow).BarcodeDetector
}

function browserMediaDevices(): Pick<MediaDevices, 'getUserMedia'> | undefined {
  if (typeof navigator === 'undefined') return undefined
  return navigator.mediaDevices
}

// This feature check is intentionally exported for the resolver's progressive enhancement.
// oxlint-disable-next-line react/only-export-components
export function scannerSupported(): boolean {
  return Boolean(browserDetector() && browserMediaDevices()?.getUserMedia)
}

export function RobotScanner({
  open,
  onCancel,
  onDetected,
  mediaDevices = browserMediaDevices(),
  Detector = browserDetector(),
}: RobotScannerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const frameRef = useRef<number | null>(null)
  const generationRef = useRef(0)
  const [error, setError] = useState<string | null>(null)

  const stopCamera = useCallback(() => {
    generationRef.current += 1
    if (frameRef.current != null) {
      window.cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }, [])

  const cancel = useCallback(() => {
    stopCamera()
    onCancel()
  }, [onCancel, stopCamera])

  useEffect(() => {
    if (!open) {
      stopCamera()
      return
    }
    if (!mediaDevices || !Detector) {
      setError('Сканирование камерой недоступно в этом браузере.')
      return stopCamera
    }

    setError(null)
    const generation = ++generationRef.current
    let disposed = false
    const active = () => !disposed && generation === generationRef.current
    let detector: BarcodeDetectorLike
    try {
      detector = new Detector({ formats: ['qr_code', 'code_128'] })
    } catch {
      setError('Не удалось открыть сканер. Введите номер робота вручную.')
      return stopCamera
    }

    const fail = () => {
      if (!active()) return
      setError('Не удалось открыть камеру. Введите номер робота вручную.')
      stopCamera()
    }

    const scheduleDetection = () => {
      if (!active()) return
      frameRef.current = window.requestAnimationFrame(() => {
        if (!active() || !videoRef.current) return
        void detector.detect(videoRef.current).then((codes) => {
          if (!active()) return
          const value = codes.find((code) => code.rawValue?.trim())?.rawValue?.trim()
          if (value) {
            stopCamera()
            onDetected(value)
            return
          }
          scheduleDetection()
        }).catch(fail)
      })
    }

    void mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    }).then((stream) => {
      if (!active()) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const video = videoRef.current
      if (!video) {
        stopCamera()
        return
      }
      video.srcObject = stream
      return video.play()
    }).then(() => {
      if (active()) scheduleDetection()
    }).catch(fail)

    return () => {
      disposed = true
      stopCamera()
    }
  }, [Detector, mediaDevices, onDetected, open, stopCamera])

  return (
    <BottomSheet
      onOpenChange={(nextOpen) => {
        if (!nextOpen) cancel()
      }}
      open={open}
      title="Сканировать робота"
    >
      <div className="rp-robot-scanner">
        <video muted playsInline ref={videoRef} />
        {error ? <p role="alert">{error}</p> : <p>Наведите камеру на код робота.</p>}
        <div className="rp-robot-scanner__actions">
          <Button onClick={cancel} type="button" variant="secondary">Отменить</Button>
          <Button onClick={cancel} type="button" variant="secondary">Ввести номер вручную</Button>
        </div>
      </div>
    </BottomSheet>
  )
}
