import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useMinuteClock } from './useMinuteClock'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

it('ticks locally, sleeps in a hidden tab, and catches up on return without polling a server', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-30T09:00:30Z'))
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  const { result, unmount } = renderHook(useMinuteClock)
  act(() => vi.advanceTimersByTime(30000))
  expect(result.current).toBe(Date.parse('2026-09-30T09:01:00Z'))
  hidden.mockReturnValue(true)
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(vi.getTimerCount()).toBe(0)
  act(() => vi.advanceTimersByTime(600000))
  expect(result.current).toBe(Date.parse('2026-09-30T09:01:00Z'))
  hidden.mockReturnValue(false)
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(result.current).toBe(Date.parse('2026-09-30T09:11:00Z'))
  expect(vi.getTimerCount()).toBe(1)
  unmount()
  expect(vi.getTimerCount()).toBe(0)
})
