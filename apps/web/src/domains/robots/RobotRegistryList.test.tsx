import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type RobotRegistry, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { RobotRegistryList } from './RobotRegistryList'

const user: User = { id: 1, role: 'mechanic', username: 'm', access_status: 'approved', parks: [], permissions: ['tracker.read', 'nav.robot_search'] }
const empty: RobotRegistry = { items: [], total: 0, offset: 0, limit: 50, has_more: false, partial: false, source_complete: true, source: 'scoped_tracker_issues', park_id: 7 }
function tree(client: { robotRegistry: (params: unknown) => Promise<RobotRegistry> }, principal = user, refreshUser = vi.fn(async () => principal)) {
  return <MemoryRouter><AuthContext.Provider value={{ user: principal, loading: false, login: async () => principal, logout: async () => undefined, refreshUser }}>
    <RobotRegistryList apiClient={client} parkId={7} scopeLoading={false} />
  </AuthContext.Provider></MemoryRouter>
}

describe('registry resource states', () => {
  it('shows loading then a stable empty result and no enabled pagination', async () => {
    let resolve!: (value: RobotRegistry) => void
    const client = { robotRegistry: vi.fn(() => new Promise<RobotRegistry>(done => { resolve = done })) }
    render(tree(client))
    expect(screen.getByText('Загружаем реестр роботов')).toBeVisible()
    resolve(empty)
    expect(await screen.findByRole('heading', { name: 'Роботы не найдены' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Назад' })).toBeDisabled()
  })

  it('retries an upstream failure and displays the new batch', async () => {
    const client = { robotRegistry: vi.fn().mockRejectedValueOnce(new ApiError(502, 'tracker_upstream_error')).mockResolvedValue(empty) }
    render(tree(client))
    expect(await screen.findByRole('alert')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByRole('heading', { name: 'Роботы не найдены' })).toBeVisible()
    expect(client.robotRegistry).toHaveBeenCalledTimes(2)
  })

  it('does not read Tracker without permission and refreshes an expired session once', async () => {
    const client = { robotRegistry: vi.fn().mockRejectedValue(new ApiError(401, 'expired')) }
    const refresh = vi.fn(async () => user)
    const view = render(tree(client, { ...user, permissions: [] }, refresh))
    expect(screen.getByRole('heading', { name: 'Реестр недоступен' })).toBeVisible()
    expect(client.robotRegistry).not.toHaveBeenCalled()
    view.rerender(tree(client, user, refresh))
    expect(await screen.findByRole('heading', { name: 'Сессия истекла' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Поиск по номеру, VIN или задаче'), { target: { value: '447' } })
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
    expect(client.robotRegistry).toHaveBeenCalledTimes(1)
  })
})
