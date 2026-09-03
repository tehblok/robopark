import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RobotScanner, type BarcodeDetectorConstructor } from './RobotScanner'

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

    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      video: { facingMode: { ideal: 'environment' } },
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
})
