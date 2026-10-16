import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { StartupGate } from './StartupGate'
import { initializeApplicationStartup, StartupTimeoutError } from './startup'
import { DesktopSessionRequiredError } from '../../pwa/desktopTransport'

function deferred() {
  let resolve!: () => void
  let reject!: (error: unknown) => void
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

afterEach(() => vi.useRealTimers())

it('renders a visible startup shell before local initialization completes', async () => {
  const pending = deferred()
  render(<StartupGate initialize={() => pending.promise}><p>Рабочее пространство</p></StartupGate>)

  expect(screen.getByRole('status')).toHaveTextContent('Запускаем локальное приложение…')
  expect(screen.queryByText('Рабочее пространство')).not.toBeInTheDocument()

  await act(async () => pending.resolve())
  expect(screen.getByText('Рабочее пространство')).toBeVisible()
})

it('bounds startup, shows a recoverable error, and retries initialization', async () => {
  vi.useFakeTimers()
  const first = deferred()
  const second = deferred()
  const initialize = vi.fn().mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise)
  render(<StartupGate initialize={initialize}><p>Рабочее пространство</p></StartupGate>)

  await act(async () => { first.reject(new StartupTimeoutError()) })
  expect(screen.getByRole('alert')).toHaveTextContent('Локальные данные пока недоступны')
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  expect(initialize).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('status')).toBeVisible()

  await act(async () => second.resolve())
  expect(screen.getByText('Рабочее пространство')).toBeVisible()
})

it('applies a deadline while keeping transport before identity hydration', async () => {
  vi.useFakeTimers()
  const order: string[] = []
  const hydrate = deferred()
  const startup = initializeApplicationStartup(
    async () => { order.push('transport') },
    async () => { order.push('identity'); await hydrate.promise },
    25,
  )
  const rejected = expect(startup).rejects.toBeInstanceOf(StartupTimeoutError)
  await act(async () => { await vi.advanceTimersByTimeAsync(25) })
  await rejected
  expect(order).toEqual(['transport', 'identity'])
  hydrate.resolve()
})

it('allows the public login shell when a direct tab lacks native capability', async () => {
  await expect(initializeApplicationStartup(
    async () => undefined,
    async () => { throw new DesktopSessionRequiredError() },
  )).resolves.toBeUndefined()
})
