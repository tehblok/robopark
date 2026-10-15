import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setDesktopRuntimeForTests } from '../../pwa/desktopRuntime'
import { DesktopAuthenticatedDownload, DesktopAuthenticatedImage } from './DesktopAuthenticatedMedia'

const DESKTOP = { version: 1 as const, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' }

beforeEach(() => {
  setDesktopRuntimeForTests(undefined)
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:authenticated-media')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
})

afterEach(() => {
  setDesktopRuntimeForTests(undefined)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('preserves native image and download URLs outside the desktop runtime', () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  render(<>
    <DesktopAuthenticatedImage alt="Фото" src="/api/inventory/parts/1/photo" />
    <DesktopAuthenticatedDownload download="report.pdf" href="/api/reports/1/attachments/2">Отчёт</DesktopAuthenticatedDownload>
  </>)

  expect(screen.getByRole('img', { name: 'Фото' })).toHaveAttribute('src', '/api/inventory/parts/1/photo')
  expect(screen.getByRole('link', { name: 'Отчёт' })).toHaveAttribute('href', '/api/reports/1/attachments/2')
  expect(fetchMock).not.toHaveBeenCalled()
})

it('loads an internal desktop image through fetch and revokes its object URL on unmount', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  const fetchMock = vi.fn(async () => new Response('pixels', { headers: { 'Content-Type': 'image/jpeg' } }))
  vi.stubGlobal('fetch', fetchMock)

  const view = render(<DesktopAuthenticatedImage alt="Фото" src="/api/inventory/parts/1/photo" />)

  expect(await screen.findByRole('img', { name: 'Фото' })).toHaveAttribute('src', 'blob:authenticated-media')
  expect(fetchMock).toHaveBeenCalledWith('/api/inventory/parts/1/photo', expect.objectContaining({
    cache: 'no-store', credentials: 'same-origin', signal: expect.any(AbortSignal),
  }))
  view.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:authenticated-media')
})

it('does not expose a raw internal image URL after a failed desktop fetch', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('no', { status: 403 })))

  render(<DesktopAuthenticatedImage alt="Фото" src="/api/inventory/parts/1/photo" />)

  await waitFor(() => expect(screen.getByRole('img', { name: 'Фото' })).toHaveAttribute('data-media-state', 'error'))
  expect(screen.getByRole('img', { name: 'Фото' })).not.toHaveAttribute('src', '/api/inventory/parts/1/photo')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('abandons a stale image request when the source changes', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  let resolveOld!: (response: Response) => void
  const oldResponse = new Promise<Response>(resolve => { resolveOld = resolve })
  const fetchMock = vi.fn()
    .mockReturnValueOnce(oldResponse)
    .mockResolvedValueOnce(new Response('new', { headers: { 'Content-Type': 'image/png' } }))
  vi.stubGlobal('fetch', fetchMock)

  const view = render(<DesktopAuthenticatedImage alt="Фото" src="/api/images/old" />)
  const firstSignal = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal
  view.rerender(<DesktopAuthenticatedImage alt="Фото" src="/api/images/new" />)
  expect(firstSignal.aborted).toBe(true)
  await waitFor(() => expect(screen.getByRole('img', { name: 'Фото' })).toHaveAttribute('src', 'blob:authenticated-media'))
  resolveOld(new Response('old', { headers: { 'Content-Type': 'image/png' } }))
  await oldResponse
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledOnce())
})

it('rejects an image whose announced size exceeds the desktop memory limit', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('x', {
    headers: { 'Content-Length': String(17 * 1024 * 1024), 'Content-Type': 'image/jpeg' },
  })))

  render(<DesktopAuthenticatedImage alt="Большое фото" src="/api/images/large" />)

  await waitFor(() => expect(screen.getByRole('img', { name: 'Большое фото' })).toHaveAttribute('data-media-state', 'error'))
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it('downloads an internal desktop attachment through fetch without a raw navigable href', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  const fetchMock = vi.fn(async () => new Response('document', { headers: { 'Content-Type': 'application/pdf' } }))
  vi.stubGlobal('fetch', fetchMock)
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

  render(<DesktopAuthenticatedDownload download="manual.pdf" href="/api/files/manual">Инструкция</DesktopAuthenticatedDownload>)
  const link = screen.getByRole('link', { name: 'Инструкция' })
  expect(link).not.toHaveAttribute('href', '/api/files/manual')
  fireEvent.click(link)

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/files/manual', expect.objectContaining({
    cache: 'no-store', credentials: 'same-origin', signal: expect.any(AbortSignal),
  })))
  await waitFor(() => expect(click).toHaveBeenCalledOnce())
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
})

it('leaves external links untouched in the desktop runtime', () => {
  setDesktopRuntimeForTests(DESKTOP)
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  render(<DesktopAuthenticatedDownload href="https://tracker.example/file">Файл</DesktopAuthenticatedDownload>)

  expect(screen.getByRole('link', { name: 'Файл' })).toHaveAttribute('href', 'https://tracker.example/file')
  expect(fetchMock).not.toHaveBeenCalled()
})

it('never opens a download that finishes after its view is unmounted', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  let resolve!: (value: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done })))
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  const view = render(<DesktopAuthenticatedDownload href="/api/files/old">Файл</DesktopAuthenticatedDownload>)
  fireEvent.click(screen.getByRole('link', { name: 'Файл' }))
  view.unmount()
  resolve(new Response('old document', { headers: { 'Content-Type': 'application/pdf' } }))
  await new Promise(done => setTimeout(done, 0))
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(click).not.toHaveBeenCalled()
})

it('keeps a failed desktop download on the protected link', async () => {
  setDesktopRuntimeForTests(DESKTOP)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('denied', { status: 403 })))
  render(<DesktopAuthenticatedDownload href="/api/files/secret">Файл</DesktopAuthenticatedDownload>)

  fireEvent.click(screen.getByRole('link', { name: 'Файл' }))

  await waitFor(() => expect(screen.getByRole('link', { name: 'Файл' })).toHaveAttribute('data-download-state', 'error'))
  expect(screen.getByRole('link', { name: 'Файл' })).toHaveAttribute('href', '#')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
