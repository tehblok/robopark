import { act, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { resourceStore } from '../../lib/resource'
import { MaintenanceGate } from './MaintenanceGate'

afterEach(() => { resourceStore.clearAll(); vi.useRealTimers(); vi.restoreAllMocks() })
it('checks immediately, spreads its first refresh across 30–60 seconds and pauses offline', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  const load = vi.spyOn(api, 'opsMaintenance').mockResolvedValue({ active: false } as Awaited<ReturnType<typeof api.opsMaintenance>>)
  render(<MaintenanceGate />)
  await act(async () => {})
  await act(() => vi.advanceTimersByTimeAsync(44_999))
  expect(load).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(load).toHaveBeenCalledTimes(2)
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  await act(() => vi.advanceTimersByTimeAsync(120_000))
  expect(load).toHaveBeenCalledTimes(2)
})
