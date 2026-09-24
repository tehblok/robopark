import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RobotScanner, scannerSupported, type BarcodeDetectorConstructor } from './RobotScanner'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function installFrameQueue() {
  let nextId = 0
  const callbacks = new Map<number, FrameRequestCallback>()
  const request = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    nextId += 1
    callbacks.set(nextId, callback)
    return nextId
  })
  const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
    callbacks.delete(id)
  })
  return {
    flush() {
      const queued = [...callbacks.entries()]
      callbacks.clear()
      queued.forEach(([, callback]) => callback(1))
    },
    cancel,
    request,
  }
}

describe('RobotScanner', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses Foundation controls for both manual camera exits', () => {
    render(<RobotScanner onCancel={vi.fn()} onDetected={vi.fn()} open />)

    expect(screen.getByRole('button', { name: 'Отменить' })).toHaveClass(
      'rp-button',
      'rp-button--secondary',
    )
    expect(screen.getByRole('button', { name: 'Ввести номер вручную' })).toHaveClass(
      'rp-button',
      'rp-button--secondary',
    )
  })

  it('starts only while open and stops every camera track after detection', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const detect = vi.fn(async () => [{ rawValue: 'YASADR00000001975' }])
    const Detector = class { detect = detect } as unknown as BarcodeDetectorConstructor
    const onDetected = vi.fn()

    render(
      <RobotScanner
        Detector={Detector}
        mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>}
        onCancel={vi.fn()}
        onDetected={onDetected}
        open
      />,
    )

    expect(getUserMedia).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))

    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      video: { facingMode: { exact: 'environment' } },
      audio: false,
    }))
    await act(async () => undefined)
    frames.flush()
    await waitFor(() => expect(onDetected).toHaveBeenCalledWith('YASADR00000001975'))
    expect(stop).toHaveBeenCalledTimes(1)
    expect(frames.cancel).toHaveBeenCalled()
  })

  it('stops a stream that resolves after the sheet closes without publishing a detection', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const lateCamera = deferred<MediaStream>()
    const stop = vi.fn()
    const getUserMedia = vi.fn(() => lateCamera.promise)
    const onDetected = vi.fn()
    const Detector = class { detect = vi.fn(async () => [{ rawValue: '447' }]) } as unknown as BarcodeDetectorConstructor
    const view = render(
      <RobotScanner
        Detector={Detector}
        mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>}
        onCancel={vi.fn()}
        onDetected={onDetected}
        open
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))

    view.rerender(
      <RobotScanner
        Detector={Detector}
        mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>}
        onCancel={vi.fn()}
        onDetected={onDetected}
        open={false}
      />,
    )
    await act(async () => {
      lateCamera.resolve({ getTracks: () => [{ stop }] } as unknown as MediaStream)
    })
    frames.flush()

    expect(stop).toHaveBeenCalledTimes(1)
    expect(onDetected).not.toHaveBeenCalled()
    expect(frames.request).not.toHaveBeenCalled()
  })

  it('funnels cancel through camera cleanup while a detection is in flight', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const detection = deferred<Array<{ rawValue?: string }>>()
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const Detector = class { detect = vi.fn(() => detection.promise) } as unknown as BarcodeDetectorConstructor
    const onDetected = vi.fn()
    const onCancel = vi.fn()

    render(
      <RobotScanner
        Detector={Detector}
        mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>}
        onCancel={onCancel}
        onDetected={onDetected}
        open
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1))
    await act(async () => undefined)
    frames.flush()
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))
    await act(async () => {
      detection.resolve([{ rawValue: '447' }])
    })

    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(stop).toHaveBeenCalledTimes(1)
    expect(onDetected).not.toHaveBeenCalled()
  })

  it('offers camera scanning when media capture exists without BarcodeDetector', () => {
    const original = navigator.mediaDevices
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn() } })
    try {
      expect(scannerSupported()).toBe(true)
    } finally {
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: original })
    }
  })

  it('does not request camera access in an insecure context', () => {
    const getUserMedia = vi.fn()
    render(<RobotScanner mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} secureContext={false} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    expect(screen.getByText(/HTTPS/)).toBeInTheDocument()
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Ввести номер вручную' })).toBeInTheDocument()
  })

  it('uses the lazy decoder when BarcodeDetector is unavailable', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const loadFallback = vi.fn(async () => ({ detect: async () => [{ rawValue: 'YASADR00000001975' }] }))
    const onDetected = vi.fn()
    render(<RobotScanner Detector={undefined} loadFallback={loadFallback} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={onDetected} open />)
    expect(loadFallback).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(loadFallback).toHaveBeenCalledTimes(1))
    await act(async () => undefined)
    frames.flush()
    await waitFor(() => expect(onDetected).toHaveBeenCalledWith('YASADR00000001975'))
    expect(stop).toHaveBeenCalledTimes(1)
  })

  it('decodes a gallery image without requesting camera access', async () => {
    const getUserMedia = vi.fn()
    const close = vi.fn()
    const bitmap = { close } as unknown as ImageBitmap
    vi.stubGlobal('createImageBitmap', vi.fn(async () => bitmap))
    const loadFallback = vi.fn(async () => ({ detect: async (source: ImageBitmapSource) => {
      expect(source).toBe(bitmap)
      return [{ rawValue: ' YASADR00000001975 ' }]
    } }))
    const onDetected = vi.fn()
    render(<RobotScanner Detector={undefined} loadFallback={loadFallback} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={onDetected} open />)

    fireEvent.change(screen.getByLabelText('Выбрать изображение кода'), {
      target: { files: [new File(['qr'], 'robot.png', { type: 'image/png' })] },
    })

    await waitFor(() => expect(onDetected).toHaveBeenCalledWith('YASADR00000001975'))
    expect(loadFallback).toHaveBeenCalledOnce()
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalledOnce()
  })

  it('drops a late gallery detection after cancel and still closes its bitmap', async () => {
    const detection = deferred<Array<{ rawValue?: string }>>()
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ close } as unknown as ImageBitmap)))
    const onDetected = vi.fn()
    const onCancel = vi.fn()
    render(<RobotScanner Detector={undefined} loadFallback={async () => ({ detect: () => detection.promise })} mediaDevices={undefined} onCancel={onCancel} onDetected={onDetected} open />)

    fireEvent.change(screen.getByLabelText('Выбрать изображение кода'), {
      target: { files: [new File(['qr'], 'robot.png', { type: 'image/png' })] },
    })
    await waitFor(() => expect(createImageBitmap).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))
    await act(async () => detection.resolve([{ rawValue: '447' }]))

    expect(onCancel).toHaveBeenCalledOnce()
    expect(onDetected).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalledOnce()
  })

  it('drops a late gallery detection after unmount and still closes its bitmap', async () => {
    const detection = deferred<Array<{ rawValue?: string }>>()
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ close } as unknown as ImageBitmap)))
    const onDetected = vi.fn()
    const view = render(<RobotScanner Detector={undefined} loadFallback={async () => ({ detect: () => detection.promise })} mediaDevices={undefined} onCancel={vi.fn()} onDetected={onDetected} open />)

    fireEvent.change(screen.getByLabelText('Выбрать изображение кода'), {
      target: { files: [new File(['qr'], 'robot.png', { type: 'image/png' })] },
    })
    await waitFor(() => expect(createImageBitmap).toHaveBeenCalledOnce())
    view.unmount()
    await act(async () => detection.resolve([{ rawValue: '447' }]))

    expect(onDetected).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalledOnce()
  })

  it('publishes only the newest gallery selection when detections finish in reverse order', async () => {
    const first = deferred<Array<{ rawValue?: string }>>()
    const second = deferred<Array<{ rawValue?: string }>>()
    const firstClose = vi.fn()
    const secondClose = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn()
      .mockResolvedValueOnce({ close: firstClose } as unknown as ImageBitmap)
      .mockResolvedValueOnce({ close: secondClose } as unknown as ImageBitmap))
    const detect = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
    const onDetected = vi.fn()
    render(<RobotScanner Detector={undefined} loadFallback={async () => ({ detect })} mediaDevices={undefined} onCancel={vi.fn()} onDetected={onDetected} open />)
    const input = screen.getByLabelText('Выбрать изображение кода')

    fireEvent.change(input, { target: { files: [new File(['one'], 'one.png', { type: 'image/png' })] } })
    await waitFor(() => expect(detect).toHaveBeenCalledTimes(1))
    fireEvent.change(input, { target: { files: [new File(['two'], 'two.png', { type: 'image/png' })] } })
    await waitFor(() => expect(detect).toHaveBeenCalledTimes(2))
    await act(async () => second.resolve([{ rawValue: '448' }]))
    await act(async () => first.resolve([{ rawValue: '447' }]))

    expect(onDetected).toHaveBeenCalledOnce()
    expect(onDetected).toHaveBeenCalledWith('448')
    expect(firstClose).toHaveBeenCalledOnce()
    expect(secondClose).toHaveBeenCalledOnce()
  })

  it('stops live camera and keeps the selected gallery decode current', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const detection = deferred<Array<{ rawValue?: string }>>()
    const stop = vi.fn()
    const close = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const detect = vi.fn(() => detection.promise)
    const Detector = class { detect = detect } as unknown as BarcodeDetectorConstructor
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ close } as unknown as ImageBitmap)))
    const onDetected = vi.fn()
    render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={onDetected} open />)

    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce())
    fireEvent.change(screen.getByLabelText('Выбрать изображение кода'), {
      target: { files: [new File(['qr'], 'robot.png', { type: 'image/png' })] },
    })
    await waitFor(() => expect(detect).toHaveBeenCalledOnce())
    await act(async () => detection.resolve([{ rawValue: '447' }]))

    expect(stop).toHaveBeenCalledOnce()
    expect(onDetected).toHaveBeenCalledOnce()
    expect(onDetected).toHaveBeenCalledWith('447')
    expect(close).toHaveBeenCalledOnce()
  })

  it('uses the QR fallback when native BarcodeDetector rejects its formats', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream))
    const Detector = class { constructor() { throw new Error('unsupported format') } } as unknown as BarcodeDetectorConstructor
    const loadFallback = vi.fn(async () => ({ detect: async () => [{ rawValue: '447' }] }))
    const onDetected = vi.fn()
    render(<RobotScanner Detector={Detector} loadFallback={loadFallback} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={onDetected} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(loadFallback).toHaveBeenCalledOnce())
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce())
    await act(async () => undefined)
    frames.flush()
    await waitFor(() => expect(onDetected).toHaveBeenCalledWith('447'))
  })

  it('requires a new camera click after closing and reopening a successful scan', async () => {
    const frames = installFrameQueue()
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream))
    const Detector = class { detect = vi.fn(async () => [{ rawValue: '447' }]) } as unknown as BarcodeDetectorConstructor
    const onDetected = vi.fn()
    const props = { Detector, mediaDevices: { getUserMedia } as Pick<MediaDevices, 'getUserMedia'>, onCancel: vi.fn(), onDetected }
    const view = render(<RobotScanner {...props} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce())
    await act(async () => undefined)
    frames.flush()
    await waitFor(() => expect(onDetected).toHaveBeenCalledOnce())
    view.rerender(<RobotScanner {...props} open={false} />)
    view.rerender(<RobotScanner {...props} open />)
    expect(screen.getByRole('button', { name: 'Включить камеру' })).toBeVisible()
    expect(getUserMedia).toHaveBeenCalledOnce()
  })

  it('retries with a generic camera only when the rear-camera constraint is unsupported', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const getUserMedia = vi.fn().mockRejectedValueOnce(Object.assign(new Error('rear unavailable'), { name: 'OverconstrainedError' })).mockResolvedValueOnce({ getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream)
    const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
    render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2))
    expect(getUserMedia).toHaveBeenNthCalledWith(1, { video: { facingMode: { exact: 'environment' } }, audio: false })
    expect(getUserMedia).toHaveBeenNthCalledWith(2, { video: true, audio: false })
  })

  it('explains permission denial without retrying and keeps manual input', async () => {
    const getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))
    const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
    render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('разрешения'))
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Ввести номер вручную' })).toBeInTheDocument()
  })

  it('identifies a site policy block separately from a user permission denial', async () => {
    Object.defineProperty(document, 'permissionsPolicy', {
      configurable: true,
      value: { allowsFeature: (feature: string) => feature !== 'camera' },
    })
    try {
      const getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error('policy blocked'), { name: 'NotAllowedError' }))
      const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
      render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={vi.fn()} open />)
      fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
      await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Сайт запрещает доступ к камере'))
      expect(screen.getByRole('button', { name: 'Ввести номер вручную' })).toBeInTheDocument()
    } finally {
      Reflect.deleteProperty(document, 'permissionsPolicy')
    }
  })

  it('distinguishes a missing camera from denied access', async () => {
    const getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error('missing'), { name: 'NotFoundError' }))
    const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
    render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Камера не найдена'))
    expect(getUserMedia).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Ввести номер вручную' })).toBeInTheDocument()
  })

  it('stops the active camera when the page becomes hidden', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    const stop = vi.fn()
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] } as unknown as MediaStream))
    const Detector = class { detect = vi.fn(async () => []) } as unknown as BarcodeDetectorConstructor
    render(<RobotScanner Detector={Detector} mediaDevices={{ getUserMedia } as Pick<MediaDevices, 'getUserMedia'>} onCancel={vi.fn()} onDetected={vi.fn()} open />)
    fireEvent.click(screen.getByRole('button', { name: 'Включить камеру' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1))
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    try {
      fireEvent(document, new Event('visibilitychange'))
      await waitFor(() => expect(stop).toHaveBeenCalledTimes(1))
      expect(screen.getByRole('button', { name: 'Включить камеру' })).toBeInTheDocument()
    } finally {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    }
  })
})
