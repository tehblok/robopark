import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

const north = { id: 7, name: 'Северный', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }

describe('AppRouter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    installMatchMedia()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('No live requests in routing tests'))
    vi.spyOn(api, 'dashboardSummary').mockResolvedValue({ park_id: 7, generated_at: '2026-09-02T09:00:00Z', arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [] })
    vi.spyOn(api, 'operatorBlockers').mockResolvedValue({ park_id: 7, park_tag: 'north', status: 'all', counts: {}, items: [] })
    vi.spyOn(api, 'trackerIssues').mockImplementation(() => new Promise(() => undefined))
    vi.spyOn(api, 'trackerIssue').mockImplementation(() => new Promise(() => undefined))
    vi.spyOn(api, 'emergencyResolve').mockImplementation(() => new Promise(() => undefined))
  })

  afterEach(() => vi.restoreAllMocks())

  it('canonicalizes the dashboard alias into the manifest shell without stealing focus', async () => {
    const approvedOperator = testUser({
      permissions: ['nav.dashboard', 'nav.tasks', 'nav.emergency'],
      parks: [north],
    })

    renderApp('/dashboard?park=7', approvedOperator)

    expect(await screen.findByRole('heading', { name: 'Смена / Обзор' })).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=7')
    const navigation = screen.getAllByRole('navigation', { name: 'Основная навигация' })[0]
    const workLink = within(navigation).getByRole('link', { name: 'Работа' })
    expect(workLink).toHaveAttribute('href', '/work')
    expect(workLink.querySelector('svg')).not.toBeNull()
    expect(screen.getByRole('heading', { name: 'Смена / Обзор' })).not.toHaveFocus()
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

  it.each([
    ['/tasks?park=7&status=open', '/work?park=7&status=open', 'Работа'],
    ['/robots/search?q=447&park=7', '/robots?q=447&park=7', 'Роботы'],
    ['/work/ROBOPARK-42?park=7&status=open', '/work/ROBOPARK-42?park=7&status=open', 'Работа'],
    ['/robots/YASADR00000000447/check?park=7&tab=map', '/robots/YASADR00000000447/check?park=7&tab=map', 'Рабочее пространство робота'],
    ['/robots/YASADR00000000447?park=7', '/robots/YASADR00000000447?park=7', 'Рабочее пространство робота'],
  ])('registers canonical operational content for %s', async (path, expected, heading) => {
    renderApp(path, testUser({ permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'tracker.read'], parks: [north] }))
    expect(await screen.findByRole('heading', { name: heading })).toBeVisible()
    expect(screen.getByTestId('location').textContent).toBe(expected)
  })

  it.each(['/work/ROBOPARK-42?park=7', '/robots/YASADR00000000447/check?park=7', '/emergency?q=447&park=7'])('gates direct protected route %s before its data effects', async path => {
    renderApp(path, testUser({ role: 'driver', permissions: ['nav.robot_search'], parks: [north] }))
    expect(await screen.findByRole('heading', { name: 'Роботы' })).toBeVisible()
    expect(api.trackerIssue).not.toHaveBeenCalled()
    expect(api.emergencyResolve).not.toHaveBeenCalled()
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
    expect(await screen.findByRole('heading', { name: 'Смена / Обзор' })).toBeVisible()

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

  it('allows an approved user to voluntarily open the change-password form', async () => {
    renderApp('/change-password', testUser({
      permissions: ['nav.dashboard'],
      parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Смена пароля' })).toBeVisible()
    expect(screen.getByText('Вы меняете пароль по собственной инициативе.')).toBeVisible()
    expect(screen.getByRole('link', { name: /вернуться в кабинет/i })).toHaveAttribute(
      'href',
      '/overview',
    )
  })

  it('shows author and inbox workflows when both report capabilities are granted', async () => {
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([])

    renderApp('/reports', testUser({
      permissions: ['nav.reports', 'reports.create', 'reports.resolve'],
      parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Репорты' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Создать репорт' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Входящие' })).toBeVisible()
  })

  it('renders only permitted role-aware navigation', async () => {
    renderApp('/emergency', testUser({
      role: 'driver',
      permissions: ['nav.robot_search', 'nav.emergency'],
    }))

    expect(await screen.findByRole('heading', { name: 'Роботы' })).toBeVisible()
    expect(screen.getAllByRole('navigation', { name: 'Основная навигация' })[0])
      .toHaveTextContent('Роботы')
    expect(screen.queryByRole('link', { name: 'Проверка робота' })).not.toBeInTheDocument()
    expect(screen.queryByText('Аналитика')).not.toBeInTheDocument()
  })

  it('keeps catch-all redirect-to-landing semantics', async () => {
    renderApp('/missing-route', testUser({
      permissions: ['nav.dashboard', 'nav.robot_search', 'nav.emergency'],
      role: 'driver',
    }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/overview')
    })
  })
})
