import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from './ResponsiveDisclosure'

function matchMediaWidth(width: number) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()
  let matches = width < 600
  const media = {
    get matches() {
      return matches
    },
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
      if (typeof listener === 'function') {
        listeners.add(listener as (event: MediaQueryListEvent) => void)
      }
    },
    removeEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
      if (typeof listener === 'function') {
        listeners.delete(listener as (event: MediaQueryListEvent) => void)
      }
    },
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as MediaQueryList

  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue(media))

  return {
    resize(nextWidth: number) {
      matches = nextWidth < 600
      listeners.forEach((listener) => listener({ matches } as MediaQueryListEvent))
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it('opens only one disclosure on a phone', async () => {
  matchMediaWidth(390)
  const user = userEvent.setup()
  render(
    <ResponsiveDisclosureGroup label="Actions">
      <ResponsiveDisclosure id="a" title="A">Alpha</ResponsiveDisclosure>
      <ResponsiveDisclosure id="b" title="B">Beta</ResponsiveDisclosure>
    </ResponsiveDisclosureGroup>,
  )

  await user.click(screen.getByRole('button', { name: 'A' }))
  await user.click(screen.getByRole('button', { name: 'B' }))

  expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
  expect(screen.getByText('Beta')).toBeVisible()
})

it('notifies a phone workflow when its disclosure opens and closes', async () => {
  matchMediaWidth(390)
  const onOpenChange = vi.fn()
  render(<ResponsiveDisclosureGroup label="Actions"><ResponsiveDisclosure id="a" onOpenChange={onOpenChange} title="A">Alpha</ResponsiveDisclosure></ResponsiveDisclosureGroup>)

  await userEvent.click(screen.getByRole('button', { name: 'A' }))
  await userEvent.click(screen.getByRole('button', { name: 'A' }))

  expect(onOpenChange.mock.calls).toEqual([[true], [false]])
})

it('shows every disclosure on wider screens', () => {
  matchMediaWidth(600)
  render(
    <ResponsiveDisclosureGroup label="Actions">
      <ResponsiveDisclosure id="a" title="A">Alpha</ResponsiveDisclosure>
      <ResponsiveDisclosure id="b" title="B" summary="Second action">Beta</ResponsiveDisclosure>
    </ResponsiveDisclosureGroup>,
  )

  expect(screen.getByText('Alpha')).toBeVisible()
  expect(screen.getByText('Beta')).toBeVisible()
  expect(screen.getByText('Second action')).toBeVisible()
})

it('preserves the selected disclosure while resizing', async () => {
  const viewport = matchMediaWidth(390)
  const user = userEvent.setup()
  render(
    <ResponsiveDisclosureGroup initialOpenId="a" label="Actions">
      <ResponsiveDisclosure id="a" title="A">Alpha</ResponsiveDisclosure>
      <ResponsiveDisclosure id="b" title="B">Beta</ResponsiveDisclosure>
    </ResponsiveDisclosureGroup>,
  )

  await user.click(screen.getByRole('button', { name: 'B' }))
  act(() => viewport.resize(600))
  act(() => viewport.resize(390))

  expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
  expect(screen.getByText('Beta')).toBeVisible()
  expect(screen.getByRole('button', { name: 'B' })).toHaveAttribute('aria-expanded', 'true')
})

it('does not change the mobile selection when a desktop trigger is clicked', async () => {
  const viewport = matchMediaWidth(390)
  const user = userEvent.setup()
  render(
    <ResponsiveDisclosureGroup initialOpenId="a" label="Actions">
      <ResponsiveDisclosure id="a" title="A">Alpha</ResponsiveDisclosure>
      <ResponsiveDisclosure id="b" title="B">Beta</ResponsiveDisclosure>
    </ResponsiveDisclosureGroup>,
  )

  act(() => viewport.resize(600))
  const trigger = screen.getByRole('button', { name: 'A' })
  expect(trigger).toBeDisabled()
  await user.click(trigger)
  act(() => viewport.resize(390))

  expect(screen.getByText('Alpha')).toBeVisible()
  expect(screen.queryByText('Beta')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'A' })).toBeEnabled()
})
