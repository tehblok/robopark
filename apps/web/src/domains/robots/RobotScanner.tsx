import { type ChangeEvent, useCallback, useEffect, useRef, useState } from 'react'
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
  loadFallback?: () => Promise<BarcodeDetectorLike>
  secureContext?: boolean
}

function browserDetector(): BarcodeDetectorConstructor | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as DetectorWindow).BarcodeDetector
}

function browserMediaDevices(): Pick<MediaDevices, 'getUserMedia'> | undefined {
  if (typeof navigator === 'undefined') return undefined
  return navigator.mediaDevices
}

function browserSecureContext(): boolean {
  if (typeof window === 'undefined') return false
  if (typeof window.isSecureContext === 'boolean') return window.isSecureContext
  return window.location.protocol === 'https:' || ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
}

async function loadBrowserFallback(): Promise<BarcodeDetectorLike> {
  const { createQrDetector } = await import('./robotScannerFallback')
  return createQrDetector()
}

function cameraError(reason: unknown): string {
  const name = reason instanceof Error ? reason.name : ''
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    const policyDocument = document as Document & {
      permissionsPolicy?: { allowsFeature: (feature: string) => boolean }
      featurePolicy?: { allowsFeature: (feature: string) => boolean }
    }
    const policy = policyDocument.permissionsPolicy ?? policyDocument.featurePolicy
    if (policy && !policy.allowsFeature('camera')) {
      return 'Сайт запрещает доступ к камере. Обратитесь к администратору или введите номер вручную.'
    }
    return 'Нет разрешения на камеру. Разрешите доступ в настройках браузера или введите номер вручную.'
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'Камера не найдена. Подключите камеру или введите номер вручную.'
  }
  if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'SecurityError') {
    return 'Камера занята или заблокирована системой. Проверьте настройки устройства или введите номер вручную.'
  }
  return 'Не удалось открыть камеру. Введите номер робота вручную.'
}

// This feature check is intentionally exported for the resolver's progressive enhancement.
// oxlint-disable-next-line react/only-export-components
export function scannerSupported(): boolean {
  return Boolean(browserSecureContext() && browserMediaDevices()?.getUserMedia)
}

export function RobotScanner({
  open,
  onCancel,
  onDetected,
  mediaDevices = browserMediaDevices(),
  Detector = browserDetector(),
  loadFallback = loadBrowserFallback,
  secureContext = browserSecureContext(),
}: RobotScannerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const frameRef = useRef<number | null>(null)
  const generationRef = useRef(0)
  const [started, setStarted] = useState(false)
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
    setStarted(false)
    setError(null)
    onCancel()
  }, [onCancel, stopCamera])

  const createDetector = useCallback(async (): Promise<BarcodeDetectorLike> => {
    if (!Detector) return loadFallback()
    try {
      return new Detector({ formats: ['qr_code', 'code_128'] })
    } catch {
      return loadFallback()
    }
  }, [Detector, loadFallback])

  const scanGallery = useCallback(async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    stopCamera()
    setStarted(false)
    setError(null)
    if (typeof createImageBitmap !== 'function') {
      setError('Не удалось прочитать изображение. Введите номер робота вручную.')
      return
    }
    let bitmap: ImageBitmap | null = null
    try {
      const detector = await createDetector()
      bitmap = await createImageBitmap(file)
      const codes = await detector.detect(bitmap)
      const value = codes.find(code => code.rawValue?.trim())?.rawValue?.trim()
      if (value) onDetected(value)
      else setError('Код не найден на изображении. Выберите другой файл или введите номер вручную.')
    } catch {
      setError('Не удалось прочитать код на изображении. Выберите другой файл или введите номер вручную.')
    } finally {
      bitmap?.close()
    }
  }, [createDetector, onDetected, stopCamera])

  useEffect(() => {
    if (!open) {
      setStarted(false)
      stopCamera()
      return
    }
    if (!started) {
      stopCamera()
      return
    }
    if (!secureContext) {
      setError('Для камеры откройте сайт через HTTPS или localhost. Номер робота можно ввести вручную.')
      return stopCamera
    }
    if (!mediaDevices?.getUserMedia) {
      setError('Камера недоступна в этом браузере. Введите номер робота вручную.')
      return stopCamera
    }

    setError(null)
    const generation = ++generationRef.current
    let disposed = false
    const active = () => !disposed && generation === generationRef.current

    const fail = (reason: unknown) => {
      if (!active()) return
      setError(cameraError(reason))
      setStarted(false)
      stopCamera()
    }

    const scheduleDetection = (detector: BarcodeDetectorLike) => {
      if (!active()) return
      frameRef.current = window.requestAnimationFrame(() => {
        if (!active() || !videoRef.current) return
        void detector.detect(videoRef.current).then((codes) => {
          if (!active()) return
          const value = codes.find((code) => code.rawValue?.trim())?.rawValue?.trim()
          if (value) {
            stopCamera()
            setStarted(false)
            onDetected(value)
            return
          }
          scheduleDetection(detector)
        }).catch(fail)
      })
    }

    const start = async () => {
      let detector: BarcodeDetectorLike
      try {
        if (Detector) {
          try {
            detector = new Detector({ formats: ['qr_code', 'code_128'] })
          } catch {
            detector = await loadFallback()
          }
        } else {
          detector = await loadFallback()
        }
      } catch {
        if (active()) {
          setError('Сканер кода недоступен. Введите номер робота вручную.')
          setStarted(false)
        }
        return
      }
      if (!active()) return

      let stream: MediaStream
      try {
        stream = await mediaDevices.getUserMedia({ video: { facingMode: { exact: 'environment' } }, audio: false })
      } catch (reason) {
        const name = reason instanceof Error ? reason.name : ''
        if (name !== 'OverconstrainedError' && name !== 'NotFoundError') {
          fail(reason)
          return
        }
        try {
          stream = await mediaDevices.getUserMedia({ video: true, audio: false })
        } catch (fallbackReason) {
          fail(fallbackReason)
          return
        }
      }
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
      try {
        await video.play()
        if (active()) scheduleDetection(detector)
      } catch (reason) {
        fail(reason)
      }
    }

    const onVisibilityChange = () => {
      if (document.hidden && active()) {
        setError('Камера остановлена. Нажмите «Включить камеру», чтобы продолжить.')
        setStarted(false)
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    void start()

    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', onVisibilityChange)
      stopCamera()
    }
  }, [Detector, loadFallback, mediaDevices, onDetected, open, secureContext, started, stopCamera])

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
        {!secureContext ? <p role="alert">Для камеры откройте сайт через HTTPS или localhost. Номер робота можно ввести вручную.</p> : error ? <p role="alert">{error}</p> : started ? <p>Наведите камеру на код робота.</p> : <p>Для сканирования потребуется разрешение на камеру. Снимок никуда не отправляется.</p>}
        <div className="rp-robot-scanner__actions">
          {!started && secureContext && mediaDevices?.getUserMedia ? (
            <Button onClick={() => { setError(null); setStarted(true) }} type="button">Включить камеру</Button>
          ) : null}
          <Button onClick={() => galleryRef.current?.click()} type="button" variant="secondary">Выбрать изображение кода</Button>
          <input accept="image/*" aria-label="Выбрать изображение кода" className="issue-attach-input" onChange={scanGallery} ref={galleryRef} type="file" />
          <Button onClick={cancel} type="button" variant="secondary">Отменить</Button>
          <Button onClick={cancel} type="button" variant="secondary">Ввести номер вручную</Button>
        </div>
      </div>
    </BottomSheet>
  )
}
