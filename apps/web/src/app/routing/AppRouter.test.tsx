import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }

describe('AppRouter', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    installMatchMedia()
  })

  it('canonicalizes the dashboard alias into the manifest shell without stealing focus', async () => {
    const approvedOperator = testUser({
      permissions: ['nav.dashboard', 'nav.tasks', 'nav.emergency'],
      parks: [north],
    })

    renderApp('/dashboard', approvedOperator)

    expect(await screen.findByRole('heading', { name: /дашборд/i })).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=7')
    const navigation = screen.getAllByRole('navigation', { name: 'Основная навигация' })[0]
    const workLink = within(navigation).getByRole('link', { name: 'Работа' })
    expect(workLink).toHaveAttribute('href', '/work')
    expect(workLink.querySelector('svg')).not.toBeNull()
    expect(screen.getByRole('heading', { name: /дашборд/i })).not.toHaveFocus()
  })

  it('preserves meaningful search parameters through legacy redirects', async () => {
    renderApp('/operator?park=7', testUser({
      permissions: ['nav.dashboard'],
      parks: [north],
    }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=7')
    })
  })

  it('gates pending access before mounting the shell', async () => {
    renderApp('/overview', testUser({ access_status: 'pending' }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/access/pending')
    })
    expect(screen.queryByRole('navigation', { name: 'Основная навигация' }))
      .not.toBeInTheDocument()
  })

  it('gates a mechanic without a park before mounting the shell', async () => {
    renderApp('/overview', testUser({
      role: 'mechanic',
      parks: [],
      permissions: ['nav.dashboard'],
    }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/mechanic/no-park')
    })
  })

  it('redirects an already authorized user away from stale standalone access screens', async () => {
    renderApp('/access/pending', testUser({
      permissions: ['nav.dashboard'],
      parks: [north],
    }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=7')
    })
  })

  it('renders only permitted role-aware navigation', async () => {
    renderApp('/emergency', testUser({
      role: 'driver',
      permissions: ['nav.emergency'],
    }))

    expect(await screen.findByRole('heading', { name: 'Проверка робота' })).toBeVisible()
    expect(screen.getAllByRole('navigation', { name: 'Основная навигация' })[0])
      .toHaveTextContent('Проверка робота')
    expect(screen.queryByText('Аналитика')).not.toBeInTheDocument()
  })

  it('keeps catch-all redirect-to-landing semantics', async () => {
    renderApp('/missing-route', testUser({
      permissions: ['nav.emergency'],
      role: 'driver',
    }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/emergency')
    })
  })
})
