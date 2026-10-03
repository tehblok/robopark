import { screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { resourceStore } from '../lib/resource'
import { installMatchMedia, renderApp, testUser } from '../test/renderApp'

const north = { id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }

afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

it('redirects the retired Tracker workspace to Work without reviving manual filters', async () => {
  installMatchMedia({ width: 390 })
  renderApp('/admin/tracker?park=7', testUser({
    permissions: ['nav.dashboard', 'nav.tasks', 'nav.emergency'],
    parks: [north],
  }))

  expect(await screen.findByRole('heading', { name: 'Работа' })).toBeVisible()
  expect(screen.getByTestId('location')).toHaveTextContent('/work?park=7')
  expect(screen.queryByRole('button', { name: 'Фильтры' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Настроить политику Tracker' })).not.toBeInTheDocument()
})
