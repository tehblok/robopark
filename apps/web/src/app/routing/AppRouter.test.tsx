import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }

describe('AppRouter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('redirects a direct unauthenticated shell request to login', async () => {
    renderApp('/overview', null)

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/login')
    })
    expect(screen.queryByRole('navigation', { name: 'Основная навигация' }))
      .not.toBeInTheDocument()
  })

  it('renders one top-level fallback while shell authentication is loading', () => {
    renderApp('/overview', null, { loading: true })

    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(screen.getByRole('main')).toHaveTextContent(/загрузка/i)
    expect(api.reportsBadge).not.toHaveBeenCalled()
  })

  it('leaves the shell when the authenticated user becomes logged out', async () => {
    const app = renderApp('/overview', testUser({
      permissions: ['nav.dashboard'],
      parks: [north],
    }))
    expect(await screen.findByRole('heading', { name: /дашборд/i })).toBeVisible()

    app.rerenderAuth(null)

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/login')
    })
    expect(screen.queryByRole('navigation', { name: 'Основная навигация' }))
      .not.toBeInTheDocument()
  })

  it.each([
    ['pending', testUser({ access_status: 'pending' }), '/access/pending'],
    ['password change', testUser({ must_change_password: true }), '/change-password'],
    [
      'mechanic without park',
      testUser({ role: 'mechanic', permissions: ['nav.dashboard'], parks: [] }),
      '/mechanic/no-park',
    ],
    ['no cabinet', testUser({ role: 'driver' }), '/no-cabinet'],
  ])('redirects %s before mounting shell API effects', async (_case, user, landingPath) => {
    renderApp('/overview', user)

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(landingPath)
    })
    expect(api.reportsBadge).not.toHaveBeenCalled()
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
