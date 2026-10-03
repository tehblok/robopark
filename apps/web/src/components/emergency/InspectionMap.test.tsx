import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../design-system/theme/ThemeProvider'
import { useTheme } from '../../design-system/theme/themeContext'
import { InspectionMap } from './InspectionMap'
const mocks = vi.hoisted(() => {
  const map = { setView: vi.fn(), on: vi.fn(), invalidateSize: vi.fn(), remove: vi.fn(), panTo: vi.fn(), stop: vi.fn() }
  const marker = { addTo: vi.fn(), getLatLng: vi.fn(() => ({ lat: 55, lng: 37 })), setLatLng: vi.fn() }
  const tile = { addTo: vi.fn(), on: vi.fn() }
  return { map, marker, tile, create: vi.fn(), tiles: vi.fn() }
})
vi.mock('leaflet', () => ({ default: { Icon: { Default: { prototype: {}, mergeOptions: vi.fn() } }, map: mocks.create, marker: () => mocks.marker, tileLayer: mocks.tiles } }))
let reduced = false
const listeners = new Set<(event: MediaQueryListEvent) => void>()
function Controls() { const { setPreference } = useTheme(); return <button onClick={() => setPreference('light')}>light</button> }
function tree(lat: number | null = 55, follow = true, onUserPan = vi.fn(), visible = true) { return <ThemeProvider><Controls /><InspectionMap lat={lat} lon={37} follow={follow} onUserPan={onUserPan} visible={visible} /></ThemeProvider> }
beforeEach(() => {
  vi.clearAllMocks(); reduced = false; listeners.clear(); localStorage.clear()
  mocks.map.setView.mockReturnValue(mocks.map); mocks.create.mockReturnValue(mocks.map)
  mocks.marker.addTo.mockReturnValue(mocks.marker); mocks.tiles.mockReturnValue(mocks.tile); mocks.tile.on.mockReturnValue(mocks.tile)
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduced-motion') ? reduced : query.includes('dark'), addEventListener: (_name: string, cb: (e: MediaQueryListEvent) => void) => { if (query.includes('reduced-motion')) listeners.add(cb) }, removeEventListener: (_name: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb) }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
function motion(value: boolean) { reduced = value; act(() => listeners.forEach(listener => listener({ matches: value } as MediaQueryListEvent))) }
it('changes resolved theme on the same map and preserves provider attribution and manual pan', () => {
  const pan = vi.fn(); const view = render(tree(55, true, pan))
  expect(view.container.querySelector('.inspection-map')).toHaveAttribute('data-map-theme', 'dark')
  fireEvent.click(screen.getByRole('button', { name: 'light' }))
  expect(view.container.querySelector('.inspection-map')).toHaveAttribute('data-map-theme', 'light')
  expect(mocks.create).toHaveBeenCalledTimes(1)
  expect(mocks.tiles).toHaveBeenCalledWith('https://tile.openstreetmap.org/{z}/{x}/{y}.png', expect.objectContaining({ attribution: expect.stringContaining('OpenStreetMap'), referrerPolicy: 'strict-origin' }))
  const drag = mocks.map.on.mock.calls.find(call => call[0] === 'dragstart')![1]; drag(); expect(pan).toHaveBeenCalledOnce()
  view.rerender(tree(56, false, pan)); expect(mocks.map.panTo).not.toHaveBeenCalled()
})
it('explains failed background tiles while keeping robot coordinates available', () => {
  render(tree())
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  const tileError = mocks.tile.on.mock.calls.find(call => call[0] === 'tileerror')?.[1]
  const loading = mocks.tile.on.mock.calls.find(call => call[0] === 'loading')?.[1]
  expect(tileError).toBeTypeOf('function')
  act(() => tileError())
  expect(screen.getByRole('status')).toHaveTextContent('Подложка карты недоступна')
  expect(screen.getByRole('status')).toHaveTextContent('Координаты робота доступны')
  expect(screen.getByText(/55\.00000, 37\.00000/)).toBeVisible()
  act(() => loading())
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
it('loads map tiles only after first opening the map tab and reuses the map on return', () => {
  const pan = vi.fn()
  const view = render(tree(55, true, pan, false))
  expect(mocks.create).not.toHaveBeenCalled()
  expect(mocks.tiles).not.toHaveBeenCalled()
  view.rerender(tree(55, true, pan, true))
  expect(mocks.create).toHaveBeenCalledOnce()
  expect(mocks.tiles).toHaveBeenCalledOnce()
  expect(mocks.marker.addTo).toHaveBeenCalledOnce()
  view.rerender(tree(55, true, pan, false))
  view.rerender(tree(55, true, pan, true))
  expect(mocks.create).toHaveBeenCalledOnce()
  expect(mocks.tiles).toHaveBeenCalledOnce()
  expect(mocks.map.remove).not.toHaveBeenCalled()
})
it('creates the map when coordinates arrive after an initially empty result', () => {
  const view = render(tree(null))
  expect(mocks.create).not.toHaveBeenCalled()
  view.rerender(tree(55))
  expect(mocks.create).toHaveBeenCalledOnce()
  expect(mocks.marker.addTo).toHaveBeenCalledOnce()
})
it('reduced motion moves synchronously without RAF and follows without animation', () => {
  reduced = true
  const raf = vi.spyOn(window, 'requestAnimationFrame')
  const view = render(tree()); view.rerender(tree(56))
  expect(mocks.marker.setLatLng).toHaveBeenLastCalledWith([56, 37])
  expect(raf).not.toHaveBeenCalled()
  expect(mocks.map.panTo).toHaveBeenLastCalledWith([56, 37], { animate: false })
})
it('switching motion off cancels marker and map animation; switching back restores interpolation', () => {
  let tick!: FrameRequestCallback
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { tick = cb; return 42 })
  const cancel = vi.spyOn(window, 'cancelAnimationFrame')
  const view = render(tree()); view.rerender(tree(57))
  expect(mocks.map.panTo).toHaveBeenLastCalledWith([57, 37], { animate: true, duration: 2.4 })
  tick(performance.now() + 1250)
  expect(mocks.marker.setLatLng.mock.calls.at(-1)![0][0]).toBeGreaterThan(55)
  motion(true)
  expect(cancel).toHaveBeenCalledWith(42); expect(mocks.map.stop).toHaveBeenCalled()
  expect(mocks.marker.setLatLng).toHaveBeenLastCalledWith([57, 37])
  expect(mocks.map.panTo).toHaveBeenLastCalledWith([57, 37], { animate: false })
  motion(false); view.rerender(tree(58))
  expect(mocks.map.panTo).toHaveBeenLastCalledWith([58, 37], { animate: true, duration: 2.4 })
  expect(mocks.create).toHaveBeenCalledTimes(1)
  view.unmount(); expect(listeners.size).toBe(0)
})
