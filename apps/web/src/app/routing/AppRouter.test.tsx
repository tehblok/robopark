import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api'
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
    ['/robots/YASADR00000000447/check?park=7&tab=map', '/robots/YASADR00000000447?park=7&tab=map', 'Рабочее пространство робота'],
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
      expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=all')
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
    expect(screen.getByRole('link', { name: 'Создать репорт' })).toHaveAttribute('href', '/reports/new?park=7')
    expect(screen.getByRole('tab', { name: 'Входящие' })).toBeVisible()
    await waitFor(() => expect(api.reportsMine).toHaveBeenCalled())
    expect(Object.keys(localStorage).filter((key) => key.startsWith('robopark:res:reports:')))
      .toEqual([])
  })

  it('renders operator and admin targets together in the royal inbox', async () => {
    const south = { id: 9, name: 'Южный', tag: 'south', tracker_queue: 'SOUTH', is_active: true }
    const operatorTarget = {
      id: 31, kind: 'mechanic_problem' as const, status: 'open' as const, park_id: 7,
      author_user_id: 2, target_role: 'operator', tracker_key: null, tracker_url: null,
      title: 'Нужен оператор', body: '', parent_report_id: null, return_comment: null,
      created_at: '2026-09-04T08:00:00Z', updated_at: '2026-09-04T08:00:00Z', resolved_at: null,
    }
    const adminTarget = {
      ...operatorTarget,
      id: 32,
      park_id: 9,
      target_role: 'admin' as const,
      title: 'Нужен администратор',
    }
    const cookieAlert = {
      ...operatorTarget,
      id: 33,
      kind: 'emergency_cookie_alert',
      park_id: null,
      target_role: 'admin' as const,
      title: 'Cookie требует внимания',
    }
    vi.spyOn(api, 'parks').mockResolvedValue([north, south])
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([operatorTarget, adminTarget, cookieAlert])

    renderApp('/reports?pane=inbox&park=7', testUser({
      role: 'royal',
      permissions: ['nav.reports', 'reports.create', 'reports.resolve'],
      parks: [north, south],
    }))

    expect(await screen.findByRole('button', { name: 'Открыть репорт Нужен оператор' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Открыть репорт Нужен администратор' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Открыть репорт Cookie требует внимания' })).toBeVisible()
    expect(api.reportsInbox).toHaveBeenCalledWith(undefined)
    expect(api.reportsInbox).not.toHaveBeenCalledWith(7)
  })

  it('restores the report pane and author status filter from the canonical URL', async () => {
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([])
    const actor = userEvent.setup()
    renderApp('/reports?pane=inbox', testUser({
      permissions: ['nav.reports', 'reports.create', 'reports.resolve'],
      parks: [north],
    }))

    expect(await screen.findByRole('tab', { name: 'Входящие' })).toHaveAttribute('aria-selected', 'true')
    await actor.click(screen.getByRole('tab', { name: 'Мои репорты' }))
    await actor.selectOptions(screen.getByRole('combobox', { name: 'Статус репортов' }), 'returned')
    expect(screen.getByTestId('location')).toHaveTextContent('/reports?park=7&status=returned')
  })

  it('opens canonical report creation and direct detail routes with Reports nav current', async () => {
    const report = {
      id: 19, kind: 'mechanic_problem' as const, status: 'open' as const, park_id: 7,
      author_user_id: 1, target_role: 'operator', tracker_key: null, tracker_url: null,
      title: 'Прямой репорт', body: '', parent_report_id: null, return_comment: null,
      created_at: '2026-09-04T08:00:00Z', updated_at: '2026-09-04T08:00:00Z', resolved_at: null,
    }
    vi.spyOn(api, 'reportsMine').mockResolvedValue([])
    vi.spyOn(api, 'report').mockResolvedValue(report)
    const currentUser = testUser({
      permissions: ['nav.reports', 'reports.create'],
      parks: [north],
    })

    const createView = renderApp('/reports/new?park=7', currentUser)
    expect(await screen.findByRole('heading', { name: 'Создать репорт' })).toBeVisible()
    expect(screen.getAllByRole('link', { name: 'Репорты' })[0]).toHaveAttribute('aria-current', 'page')
    createView.unmount()

    renderApp('/reports/19?park=7', currentUser)
    expect(await screen.findByRole('heading', { name: 'Прямой репорт' })).toBeVisible()
    expect(screen.getAllByRole('link', { name: 'Репорты' })[0]).toHaveAttribute('aria-current', 'page')
    expect(api.report).toHaveBeenCalledWith(19)
  })

  it('opens the only permitted reports pane and keeps inbox open-only', async () => {
    vi.spyOn(api, 'reportsInbox').mockResolvedValue([])
    renderApp('/reports', testUser({
      permissions: ['nav.reports', 'reports.resolve'],
      parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Входящие' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Создать репорт' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Статус репортов' })).not.toBeInTheDocument()
  })

  it('labels an author report with its own park rather than the current shell park', async () => {
    const actor = userEvent.setup()
    const south = { id: 8, name: 'Южный', tag: 'south', tracker_queue: 'SOUTH', is_active: true }
    const report = {
      id: 19, kind: 'mechanic_problem' as const, status: 'open' as const, park_id: 8,
      author_user_id: 1, target_role: 'operator', tracker_key: null, tracker_url: null,
      title: 'В другом парке', body: '', parent_report_id: null, return_comment: null,
      created_at: '2026-09-04T08:00:00Z', updated_at: '2026-09-04T08:00:00Z', resolved_at: null,
    }
    vi.spyOn(api, 'reportsMine').mockResolvedValue([report])
    vi.spyOn(api, 'report').mockResolvedValue(report)
    renderApp('/reports?park=7', testUser({
      permissions: ['nav.reports', 'reports.create'], parks: [north, south],
    }))

    await screen.findByRole('button', { name: /в другом парке/i })
    await actor.click(screen.getByRole('button', { name: /в другом парке/i }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/reports/19?park=7'))
    expect(within(await screen.findByRole('region', { name: 'Детали' })).getByText('Южный')).toBeVisible()
  })

  it('opens the canonical robot-check settings route from configuration recovery', async () => {
    const actor = userEvent.setup()
    vi.mocked(api.emergencyResolve).mockResolvedValue({
      vin: 'YASADR00000000447',
      sections: [],
    })
    vi.spyOn(api, 'emergencySnapshot').mockRejectedValue(
      new ApiError(403, 'emergency_cookie_invalid', 'config-id'),
    )
    vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([])

    renderApp('/robots/YASADR00000000447/check?tab=map', testUser({
      role: 'field_lead',
      permissions: ['nav.emergency', 'nav.admin.emergency'],
      parks: [north],
    }))

    const settings = await screen.findByRole('link', { name: 'Открыть настройки' })
    expect(settings).toHaveAttribute('href', '/admin/emergency/config')
    await actor.click(settings)
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/admin/emergency/config'))
    expect(await screen.findByRole('heading', { name: 'Настройки проверки робота' })).toBeVisible()
    expect(document.body).not.toHaveTextContent(/Аварийный режим/i)
    expect(screen.queryByText(/^(?:Emergency|Конфиг Emergency|Разделы Emergency)/i)).not.toBeInTheDocument()
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
