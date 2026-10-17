import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { User } from '../api'
import { OfflineWorkspaceControl } from './OfflineWorkspaceControl'

const user = { id: 7, username: 'operator', role: 'operator', access_status: 'approved', permissions: ['tracker.read'], parks: [
  { id: 1, name: 'First', tag: 'first', timezone: 'Europe/Moscow' },
  { id: 2, name: 'Second', tag: 'second', timezone: 'Europe/Moscow' },
] } as User

it('prepares every authorized park and reports freshness and bounded counts', async () => {
  const prepare = vi.fn(async () => ({ version: 1 as const, state: 'ready' as const, preparedAt: Date.now(), parkCount: 2, taskCount: 12, robotCount: 5, complete: true }))
  render(<OfflineWorkspaceControl user={user} prepare={prepare} loadState={async () => undefined} />)
  fireEvent.click(screen.getByRole('button', { name: 'Подготовить офлайн' }))
  await waitFor(() => expect(prepare).toHaveBeenCalledWith([1, 2], undefined))
  expect(screen.getByText(/2 парка · 12 задач/)).toBeVisible()
  expect(screen.getByText(/Обновлено:/)).toBeVisible()
})

it('keeps the previous snapshot visible after a reconnect refresh failure', async () => {
  const previous = { version: 1 as const, state: 'ready' as const, preparedAt: Date.now() - 60_000, parkCount: 1, taskCount: 4, robotCount: 2, complete: true }
  const prepare = vi.fn(async () => ({ ...previous, state: 'stale' as const }))
  render(<OfflineWorkspaceControl user={user} prepare={prepare} loadState={async () => previous} />)
  await screen.findByText(/1 парк · 4 задачи/)
  fireEvent(window, new Event('online'))
  await waitFor(() => expect(prepare).toHaveBeenCalledWith([1, 2], previous))
  expect(screen.getByText(/Сохранённые данные устарели/)).toBeVisible()
})

it('marks terminal and other unsupported operations as online-only', async () => {
  render(<OfflineWorkspaceControl user={user} prepare={vi.fn()} loadState={async () => undefined} />)
  expect(screen.getByText(/Терминал, администрирование и свежая телеметрия доступны только с сетью/)).toBeVisible()
})

it('labels a retained preparation older than twelve hours as stale with its original age', async () => {
  const previous = { version: 1 as const, state: 'ready' as const, preparedAt: Date.now() - 13 * 60 * 60 * 1000, parkCount: 1, taskCount: 4, robotCount: 2, complete: true }
  render(<OfflineWorkspaceControl user={user} prepare={vi.fn()} loadState={async () => previous} />)
  expect(await screen.findByText(/Сохранённые данные устарели \(от/)).toBeVisible()
})
