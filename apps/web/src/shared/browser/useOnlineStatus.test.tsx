import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useOnlineStatus } from './useOnlineStatus'

function Probe() {
  return <output>{useOnlineStatus() ? 'online' : 'offline'}</output>
}

describe('useOnlineStatus', () => {
  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
  })

  it('tracks browser online and offline events', () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    render(<Probe />)
    expect(screen.getByText('online')).toBeInTheDocument()

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })
    act(() => window.dispatchEvent(new Event('offline')))
    expect(screen.getByText('offline')).toBeInTheDocument()

    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
    act(() => window.dispatchEvent(new Event('online')))
    expect(screen.getByText('online')).toBeInTheDocument()
  })
})
