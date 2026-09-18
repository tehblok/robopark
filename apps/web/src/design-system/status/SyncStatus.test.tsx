import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SyncStatus } from './SyncStatus'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })
it('stays quiet while healthy and reports connection changes without a refresh button', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-06T12:00:00Z'))
  const updatedAt = Date.now() - 120_000
  const view = render(<SyncStatus updatedAt={updatedAt} />)
  expect(screen.queryByText(/Синхронизировано/)).not.toBeInTheDocument()
  view.rerender(<SyncStatus updatedAt={updatedAt} error={new Error('unavailable')} />)
  expect(screen.getByText('Синхронизация задерживается')).toBeInTheDocument()
  act(() => { vi.advanceTimersByTime(60_000) })
  expect(screen.queryByText(/Синхронизировано/)).not.toBeInTheDocument()
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  act(() => { window.dispatchEvent(new Event('offline')) })
  expect(screen.getByText('Нет сети')).toBeInTheDocument()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})
it('shows an active first synchronization without claiming success', () => {
  render(<SyncStatus updatedAt={null} isRevalidating />)
  expect(screen.getByText('Синхронизация…')).toBeInTheDocument()
})
