import { act, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ApiTimeoutError, type User } from '../../api'
import { WorkRobotCheck } from './WorkRobotCheck'

const user: User = {
  id: 3,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.emergency'],
  parks: [],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

it('keeps a resolve timeout visible when auth republishes the same user', async () => {
  const apiClient = {
    emergencyResolve: vi.fn()
      .mockRejectedValueOnce(new ApiTimeoutError(30_000))
      .mockImplementation(() => new Promise<never>(() => undefined)),
    emergencySnapshot: vi.fn(() => new Promise<never>(() => undefined)),
    emergencySection: vi.fn(() => new Promise<never>(() => undefined)),
  }
  const props = {
    robot: '447',
    activeTab: 'scheme',
    apiClient,
    onOpenTasks: vi.fn(),
    onTabChange: vi.fn(),
  }
  const view = render(<WorkRobotCheck {...props} user={user} />)
  expect(await screen.findByRole('heading', { name: 'Сервис не ответил вовремя' })).toBeVisible()

  view.rerender(<WorkRobotCheck {...props} user={{ ...user, parks: [...user.parks] }} />)

  expect(screen.getByRole('heading', { name: 'Сервис не ответил вовремя' })).toBeVisible()
  expect(screen.queryByText('Находим робота')).not.toBeInTheDocument()
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
})

it('publishes the original pending resolve after auth republishes the same user', async () => {
  const pending = deferred<{ vin: string; sections: [] }>()
  const apiClient = {
    emergencyResolve: vi.fn(() => pending.promise),
    emergencySnapshot: vi.fn(() => new Promise<never>(() => undefined)),
    emergencySection: vi.fn(() => new Promise<never>(() => undefined)),
  }
  const props = { robot: '447', apiClient, onOpenTasks: vi.fn(), onTabChange: vi.fn() }
  const view = render(<WorkRobotCheck {...props} user={user} />)
  await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))

  view.rerender(<WorkRobotCheck {...props} user={{ ...user, parks: [...user.parks] }} />)
  await act(async () => pending.resolve({ vin: 'YASADR00000000447', sections: [] }))

  expect(screen.getByTestId('robot-check-layout')).toBeVisible()
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
})

it('publishes the original pending failure after auth republishes the same user', async () => {
  const pending = deferred<never>()
  const apiClient = {
    emergencyResolve: vi.fn(() => pending.promise),
    emergencySnapshot: vi.fn(() => new Promise<never>(() => undefined)),
    emergencySection: vi.fn(() => new Promise<never>(() => undefined)),
  }
  const props = { robot: '447', apiClient, onOpenTasks: vi.fn(), onTabChange: vi.fn() }
  const view = render(<WorkRobotCheck {...props} user={user} />)
  await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))

  view.rerender(<WorkRobotCheck {...props} user={{ ...user, parks: [...user.parks] }} />)
  await act(async () => pending.reject(new ApiTimeoutError(30_000)))

  expect(screen.getByRole('heading', { name: 'Сервис не ответил вовремя' })).toBeVisible()
  expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1)
})

it('ignores the previous account resolve after the principal changes', async () => {
  const previous = deferred<{ vin: string; sections: [] }>()
  const apiClient = {
    emergencyResolve: vi.fn()
      .mockImplementationOnce(() => previous.promise)
      .mockImplementation(() => new Promise<never>(() => undefined)),
    emergencySnapshot: vi.fn(() => new Promise<never>(() => undefined)),
    emergencySection: vi.fn(() => new Promise<never>(() => undefined)),
  }
  const props = { robot: '447', apiClient, onOpenTasks: vi.fn(), onTabChange: vi.fn() }
  const view = render(<WorkRobotCheck {...props} user={user} />)
  await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(1))

  view.rerender(<WorkRobotCheck {...props} user={{ ...user, id: 4, username: 'replacement' }} />)
  await waitFor(() => expect(apiClient.emergencyResolve).toHaveBeenCalledTimes(2))
  await act(async () => previous.resolve({ vin: 'YASADR00000000447', sections: [] }))

  expect(screen.getByRole('status', { name: 'Находим робота' })).toBeVisible()
  expect(screen.queryByTestId('robot-check-layout')).not.toBeInTheDocument()
})
