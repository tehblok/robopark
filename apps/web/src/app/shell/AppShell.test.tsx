import { useLayoutEffect } from 'react'
import { readFileSync } from 'node:fs'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ThemeProvider } from '../../design-system/theme/ThemeProvider'
import { ParkProvider } from '../../ParkProvider'
import { ParkScopeContext } from '../park/parkScope'
import {
  installMatchMedia,
  renderApp,
  testUser,
  type MatchMediaController,
} from '../../test/renderApp'
import { AppShell } from './AppShell'
import { REPORTS_BADGE_REFRESH } from '../../reports-badge'
import { resourceStore } from '../../lib/resource'
import { INVENTORY_REVISION_CHANGED } from '../../domains/inventory/inventoryRevision'

const shellCss = readFileSync('src/app/shell/AppShell.css', 'utf8')
const overviewCss = readFileSync('src/domains/shift/overview.css', 'utf8')
const workCss = readFileSync('src/domains/work/work.css', 'utf8')

const north = { id: 7, name: 'Северный', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
const operator = testUser({
  permissions: [
    'nav.dashboard',
    'nav.tasks',
    'nav.robot_search',
    'nav.emergency',
    'nav.reports',
    'nav.analytics',
    'tracker.read',
  ],
  parks: [north],
})

function HistoryControls() {
  const navigate = useNavigate()
  return (
    <>
      <button onClick={() => navigate(-1)} type="button">Назад в истории</button>
      <button onClick={() => navigate(1)} type="button">Вперёд в истории</button>
      <button onClick={() => navigate('/overview', { replace: true })} type="button">
        Заменить на обзор
      </button>
    </>
  )
}

function renderShellPath(path: string, currentUser = operator) {
  const refreshUser = vi.fn().mockResolvedValue(currentUser)
  const tree = (nextUser: User) => (
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <AuthContext.Provider value={{ user: nextUser, loading: false,
          login: vi.fn(), refreshUser, logout: vi.fn() }}>
          <ParkProvider>
            <Routes><Route element={<AppShell />}>
              <Route path="*" element={<h1>Рабочий экран</h1>} />
            </Route></Routes>
          </ParkProvider>
        </AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>
  )
  const result = render(tree(currentUser))
  return { ...result, refreshUser, rerenderAuth: (nextUser: User) => result.rerender(tree(nextUser)) }
}

it('offers installation in More and triggers the browser install prompt', async () => {
  installMatchMedia({ width: 390 })
  const prompt = vi.fn().mockResolvedValue(undefined)
  const installEvent = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: () => Promise<void>
    userChoice: Promise<{ outcome: string }>
  }
  installEvent.prompt = prompt
  installEvent.userChoice = Promise.resolve({ outcome: 'accepted' })
  fireEvent(window, installEvent)
  renderShellPath('/overview')
  fireEvent.click(screen.getByRole('button', { name: 'Ещё' }))
  fireEvent.click(screen.getByRole('button', { name: 'Установить приложение' }))
  await waitFor(() => expect(prompt).toHaveBeenCalledOnce())
})

it('shows manual installation help when the browser omits an install prompt', () => {
  installMatchMedia({ width: 390 })
  renderShellPath('/overview')
  fireEvent.click(screen.getByRole('button', { name: 'Ещё' }))
  fireEvent.click(screen.getByRole('button', { name: 'Установить приложение' }))
  expect(screen.getByRole('status')).toHaveTextContent('Добавить на главный экран')
})

function renderShellWithParkScope(
  role: User['role'],
  setParkId = vi.fn(),
  availableParks?: (typeof north)[],
  allowAllParks = false,
  path = '/overview?park=7',
  permissions: string[] = [],
) {
  const south = { id: 9, name: 'Южный', tag: 'south', is_active: true }
  const parks = availableParks ?? [north, south]
  const currentUser = testUser({ role, parks, permissions })
  return {
    setParkId,
    ...render(
      <MemoryRouter initialEntries={[path]}>
        <ThemeProvider>
          <AuthContext.Provider value={{ user: currentUser, loading: false,
            login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}>
            <ParkScopeContext.Provider value={{
              allowAllParks,
              parkId: allowAllParks ? null : 7,
              selectedPark: allowAllParks ? null : north,
              parks,
              loading: false,
              locked: false,
              setParkId,
              refreshParks: vi.fn(),
            }}>
              <Routes><Route element={<AppShell />}>
                <Route path="*" element={<h1>Рабочий экран</h1>} />
              </Route></Routes>
            </ParkScopeContext.Provider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>,
    ),
  }
}



function BadgeCommitProbe({ parkId, snapshots }: { parkId: number; snapshots: string[] }) {
  useLayoutEffect(() => {
    const badges = Array.from(document.querySelectorAll('.rp-shell__badge'))
      .map((badge) => badge.textContent ?? '')
      .join(',')
    snapshots.push(`${parkId}:${badges}`)
  })
  return null
}

function declaredCssValue(element: Element, property: string): string {
  let value = ''
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      if ('selectorText' in rule && 'style' in rule) {
        const styleRule = rule as CSSStyleRule
        if (element.matches(styleRule.selectorText)) {
          value = styleRule.style.getPropertyValue(property) || value
        }
      }
    }
  }
  return value
}

describe('AppShell', () => {
  it('shows the product identity and developer attribution', async () => {
    const actor = userEvent.setup()
    renderShellPath('/work')
    expect(screen.getByLabelText('Система управления робопарками')).toHaveTextContent('Парки')
    await actor.click(screen.getByRole('button', { name: 'Ещё' }))
    expect(screen.getByText(/tehblokdan/)).toBeVisible()
  })

  it('puts Overview, Work, Robots and Campaigns first for operators', () => {
    act(() => media.setWidth(390))
    renderShellPath('/work', testUser({
      role: 'operator', parks: [north],
      permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search'],
    }))
    const navigation = screen.getAllByRole('navigation', { name: 'Основная навигация' })[1]
    expect(within(navigation).getAllByRole('link').map(link => link.textContent)).toEqual([
      'Обзор', 'Работа', 'Роботы', 'СК и оклейка',
    ])
  })

  it('shows a separate My Tasks destination to mechanics on phone and desktop', () => {
    act(() => media.setWidth(390))
    renderShellPath('/work?view=mine', testUser({
      role: 'mechanic', parks: [north], permissions: ['nav.tasks', 'tracker.read'],
    }))
    for (const navigation of screen.getAllByRole('navigation', { name: 'Основная навигация' })) {
      expect(within(navigation).getByRole('link', { name: 'Мои задачи' }))
        .toHaveAttribute('href', '/work?view=mine')
    }
  })

  it('does not expose the retired Startrek workspace in navigation', () => {
    renderShellPath('/work', testUser({
      role: 'admin', parks: [north], permissions: ['nav.tasks', 'nav.admin.tracker'],
    }))
    expect(screen.queryByRole('link', { name: /Startrek/ })).not.toBeInTheDocument()
  })
  it.each(['admin', 'royal', 'operator'])('offers all and a specific park for %s even with one accessible park', async role => {
    const { setParkId } = renderShellWithParkScope(role, vi.fn(), [north], true)
    fireEvent.click(screen.getByRole('button', { name: 'Сменить парк' }))
    expect(screen.getByRole('option', { name: 'Все доступные парки' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('option', { name: north.name }))
    expect(setParkId).toHaveBeenCalledWith(north.id)
    fireEvent.click(screen.getByRole('button', { name: 'Сменить парк' }))
    fireEvent.click(screen.getByRole('option', { name: 'Все доступные парки' }))
    expect(setParkId).toHaveBeenCalledWith(null)
  })

  let media: MatchMediaController

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    media = installMatchMedia({ width: 1200 })
    vi.spyOn(api, 'reportsBadge').mockResolvedValue({ count: 0 })
    vi.spyOn(api, 'changeRevision').mockResolvedValue({ revision: 0 })
    vi.spyOn(api, 'dashboardSummary').mockResolvedValue({ park_id: 7, generated_at: '2026-09-02T09:00:00Z', arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [] })
    vi.spyOn(api, 'operatorBlockers').mockResolvedValue({ park_id: 7, park_tag: 'north', status: 'all', counts: {}, items: [] })
    vi.spyOn(api, 'trackerIssues').mockResolvedValue({ items: [], total: 0, limit: 30, offset: 0, has_more: false })
    vi.spyOn(api, 'operationsOverview').mockResolvedValue({ park_id: 7, generated_at: '2026-09-02T09:00:00Z', timezone: 'Europe/Moscow', selected_status: 'all', status_options: [{ key: 'all', label: 'Все доступные' }, { key: 'new', label: 'Новые' }], counts: { all: 1, new: 1 }, tasks: [{ key: 'ROBOPARK-1', summary: 'Проверить робота', status: 'Новый', bucket: 'new', robot: '447', created_at: null, hours_created: null, url: '' }], tasks_total: 1, tasks_truncated: false, flow: { definition_version: 2, window_start: '2026-09-01T09:00:00Z', window_end: '2026-09-02T09:00:00Z', expected_buckets: 12, observed_buckets: 0, complete: false, legacy_buckets: 0, points: [] }, sla: { target_hours: null, evaluated_count: 0, unknown_count: 1, at_risk_count: null, overdue_count: null, overdue: [], overdue_truncated: false }, workload: null, operators: null })
  })

  it('checks only the visible work version and refreshes mounted resources on change', async () => {
    vi.mocked(api.changeRevision)
      .mockResolvedValueOnce({ revision: 2 })
      .mockResolvedValueOnce({ revision: 3 })
    const refresh = vi.spyOn(resourceStore, 'revalidate')
    renderShellPath('/work')
    await waitFor(() => expect(api.changeRevision).toHaveBeenCalledWith('work:mine'))
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(refresh).toHaveBeenCalledWith(`work:${operator.id}:`, { prefix: true }))
    refresh.mockRestore()
  })

  it('publishes a park-scoped inventory refresh when its version changes', async () => {
    vi.mocked(api.changeRevision)
      .mockResolvedValueOnce({ revision: 4 })
      .mockResolvedValueOnce({ revision: 5 })
    const changed = vi.fn()
    window.addEventListener(INVENTORY_REVISION_CHANGED, changed)
    renderShellWithParkScope('operator', vi.fn(), [north], false, '/inventory?park=7', ['nav.inventory'])
    await waitFor(() => expect(api.changeRevision).toHaveBeenCalledWith('inventory:7'))
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(changed).toHaveBeenCalledOnce())
    expect((changed.mock.calls[0][0] as CustomEvent<number>).detail).toBe(7)
    window.removeEventListener(INVENTORY_REVISION_CHANGED, changed)
  })

  it('does not poll park revisions while viewing inventory exports', async () => {
    renderShellWithParkScope('royal', vi.fn(), [north], false,
      '/inventory?park=7&view=export', ['nav.inventory'])

    await waitFor(() => expect(api.reportsBadge).toHaveBeenCalled())
    expect(api.changeRevision).not.toHaveBeenCalled()
  })

  it('does not restart a denied revision feed when auth refresh retains the same scope', async () => {
    vi.mocked(api.changeRevision).mockRejectedValue({ status: 403 })
    const shell = renderShellPath('/work')
    await waitFor(() => expect(shell.refreshUser).toHaveBeenCalledTimes(1))

    shell.rerenderAuth({ ...operator })
    await new Promise(resolve => window.setTimeout(resolve, 30))
    expect(api.changeRevision).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['/work/ROBOPARK-1', 'Работа'],
    ['/robots/VIN/check', 'Роботы'],
  ])('keeps the parent section current at %s on desktop and mobile', (path, label) => {
    act(() => media.setWidth(390))
    renderShellPath(path)
    for (const navigation of screen.getAllByRole('navigation', { name: 'Основная навигация' })) {
      expect(within(navigation).getByRole('link', { name: label }))
        .toHaveAttribute('aria-current', 'page')
      expect(navigation.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
    }
  })

  it('does not activate a route with only a shared text prefix', () => {
    renderShellPath('/workbench')
    expect(screen.getByRole('link', { name: 'Работа' })).not.toHaveAttribute('aria-current')
  })

  it('keeps More current after entering a secondary route and closing the sheet', async () => {
    const actor = userEvent.setup()
    act(() => media.setWidth(390))
    renderShellPath('/overview')
    await actor.click(screen.getByRole('button', { name: 'Ещё' }))
    const dialog = screen.getByRole('dialog', { name: 'Ещё' })
    await actor.click(within(dialog).getByRole('link', { name: 'Репорты' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ещё' })).toHaveClass('is-active')
    expect(screen.getByRole('button', { name: 'Ещё' })).toHaveAttribute('aria-current', 'page')
  })

  it('uses the accessible More sheet for appearance and secondary navigation', async () => {
    const actor = userEvent.setup()
    localStorage.setItem('robopark-theme', 'dark')
    renderApp('/overview', operator)

    await actor.click(screen.getByRole('button', { name: 'Ещё' }))
    const dialog = screen.getByRole('dialog', { name: 'Ещё' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement | null)

    const theme = within(dialog).getByRole('radiogroup', { name: 'Тема оформления' })
    await actor.click(within(theme).getByRole('radio', { name: 'Системная' }))
    expect(localStorage.getItem('robopark-theme')).toBe('system')
    await actor.click(within(theme).getByRole('radio', { name: 'Светлая' }))
    expect(document.documentElement.dataset.theme).toBe('light')
    await actor.click(within(theme).getByRole('radio', { name: 'Тёмная' }))
    expect(document.documentElement.dataset.theme).toBe('dark')

    const density = within(dialog).getByRole('radiogroup', { name: 'Плотность интерфейса' })
    await actor.click(within(density).getByRole('radio', { name: 'Комфортная' }))
    expect(document.documentElement.dataset.density).toBe('comfortable')
    await actor.click(within(density).getByRole('radio', { name: 'Компактная' }))
    expect(document.documentElement.dataset.density).toBe('compact')
    expect(localStorage.getItem('robopark-density')).toBe('compact')

    expect(within(dialog).getByRole('link', { name: 'Репорты' }))
      .toHaveAttribute('href', '/reports')
    expect(within(dialog).getByRole('link', { name: 'Сменить пароль' }))
      .toHaveAttribute('href', '/change-password')

    act(() => media.setWidth(899))
    expect(screen.getByText('На телефоне используется комфортная плотность')).toBeVisible()
    expect(document.documentElement.dataset.density).toBe('comfortable')
    expect(localStorage.getItem('robopark-density')).toBe('compact')

    act(() => media.setWidth(1200))
    expect(document.documentElement.dataset.density).toBe('compact')
  })

  it('puts the skip link first and moves focus into the stable main landmark', async () => {
    const actor = userEvent.setup()
    renderApp('/overview', operator)

    await actor.tab()
    const skipLink = screen.getByRole('link', { name: 'К содержанию' })
    expect(skipLink).toHaveFocus()
    await actor.click(skipLink)
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
    expect(screen.getByRole('main')).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('main')).toHaveFocus()
  })

  it('focuses the destination heading after manifest navigation, including from mobile nav', async () => {
    const actor = userEvent.setup()
    renderApp('/robots', operator)

    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    const desktopWork = within(desktopNavigation).getByRole('link', { name: 'Работа' })
    desktopWork.focus()
    expect(desktopWork).toHaveFocus()

    act(() => media.setWidth(899))
    const mobileNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[1]
    await actor.click(within(mobileNavigation).getByRole('link', { name: 'Работа' }))

    const heading = await screen.findByRole('heading', { level: 1, name: 'Работа' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(desktopWork).not.toHaveFocus()
  })

  it('moves focus off a desktop navigation link when the phone layout replaces it', () => {
    renderApp('/overview', operator)

    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    const desktopWork = within(desktopNavigation).getByRole('link', { name: 'Работа' })
    desktopWork.focus()
    expect(desktopWork).toHaveFocus()

    act(() => media.setWidth(899))

    const mobileNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[1]
    expect(within(mobileNavigation).getByRole('link', { name: 'Работа' })).toHaveFocus()
  })

  it('keeps explicit accessible labels when the tablet rail is collapsed', async () => {
    const actor = userEvent.setup()
    act(() => media.setWidth(1000))
    renderApp('/overview', operator)

    await actor.click(screen.getByRole('button', { name: 'Свернуть навигацию' }))

    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    const expectedLabels = [
      ['overview', 'Обзор'],
      ['work', 'Работа'],
      ['robots', 'Роботы'],
      ['reports', 'Репорты'],
      ['analytics', 'Аналитика'],
    ] as const

    for (const [routeId, label] of expectedLabels) {
      expect(desktopNavigation.querySelector(`[data-route-id="${routeId}"]`))
        .toHaveAttribute('aria-label', label)
    }
  })

  it('prevents scroll only when restoring focus after POP history navigation', async () => {
    const actor = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <ThemeProvider>
          <AuthContext.Provider value={{
            user: operator,
            loading: false,
            login: vi.fn(),
            refreshUser: vi.fn(),
            logout: vi.fn(),
          }}>
            <ParkProvider>
              <HistoryControls />
              <Routes>
                <Route element={<AppShell />}>
                  <Route path="/overview" element={<h1>Обзор истории</h1>} />
                  <Route path="/work" element={<h1>Работа истории</h1>} />
                </Route>
              </Routes>
            </ParkProvider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>,
    )
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    const lastFocusOptions = () => focus.mock.calls[focus.mock.calls.length - 1]

    const navigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    await actor.click(within(navigation).getByRole('link', { name: 'Работа' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Работа истории' })).toHaveFocus())
    expect(lastFocusOptions()).toEqual([])

    await actor.click(screen.getByRole('button', { name: 'Назад в истории' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Обзор истории' })).toHaveFocus())
    expect(lastFocusOptions()).toEqual([{ preventScroll: true }])

    await actor.click(screen.getByRole('button', { name: 'Вперёд в истории' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Работа истории' })).toHaveFocus())
    expect(lastFocusOptions()).toEqual([{ preventScroll: true }])

    await actor.click(screen.getByRole('button', { name: 'Заменить на обзор' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Обзор истории' })).toHaveFocus())
    expect(lastFocusOptions()).toEqual([])
  })

  it('does not move focus for query-only park changes', async () => {
    const actor = userEvent.setup()
    const south = { id: 9, name: 'Южный', tag: 'south', is_active: true }
    renderApp('/overview?park=7', testUser({
      permissions: ['nav.dashboard'],
      parks: [north, south],
    }))

    const park = await screen.findByRole('button', { name: 'Сменить парк' })
    await actor.click(park)
    await actor.click(screen.getByRole('option', { name: 'Южный' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?park=9'))
    expect(screen.queryByRole('listbox', { name: 'Сменить парк' })).not.toBeInTheDocument()
  })

  it('retains park-switch trigger focus after keyboard selection', async () => {
    const actor = userEvent.setup()
    const south = { id: 9, name: 'Южный', tag: 'south', is_active: true }
    renderApp('/overview?park=7', testUser({
      permissions: ['nav.dashboard'],
      parks: [north, south],
    }))

    const trigger = await screen.findByRole('button', { name: 'Сменить парк' })
    trigger.focus()
    await actor.keyboard('{Enter}')
    await actor.keyboard('{ArrowDown}')
    expect(screen.getByRole('option', { name: 'Южный' })).toHaveFocus()

    await actor.keyboard('{Enter}')

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?park=9'))
    expect(trigger).toHaveFocus()
  })

  it.each(['ArrowDown', 'ArrowUp'])('opens the park listbox with %s and supports roving keyboard focus', async (key) => {
    const actor = userEvent.setup()
    const { setParkId } = renderShellWithParkScope('admin')
    const trigger = screen.getByRole('button', { name: 'Сменить парк' })
    trigger.focus()
    await actor.keyboard(`{${key}}`)
    expect(screen.getByRole('option', { name: 'Северный' })).toHaveFocus()
    await actor.keyboard('{End}')
    expect(screen.getByRole('option', { name: 'Южный' })).toHaveFocus()
    await actor.keyboard('{ArrowUp}')
    expect(screen.getByRole('option', { name: 'Северный' })).toHaveFocus()
    await actor.keyboard('{ArrowDown}{Home}')
    expect(screen.getByRole('option', { name: 'Северный' })).toHaveFocus()
    expect(setParkId).not.toHaveBeenCalled()
    await actor.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    await actor.keyboard('{ArrowDown}{End} ')
    expect(setParkId).toHaveBeenCalledWith(9)
    expect(trigger).toHaveFocus()
  })

  it('finds a park by typed prefix in a long list and closes when Tab leaves', async () => {
    const actor = userEvent.setup()
    const parks = Array.from({ length: 12 }, (_, index) => ({
      ...north, id: 20 + index, name: `Парк ${index + 1}`,
    }))
    renderShellWithParkScope('admin', vi.fn(), [north, ...parks, { ...north, id: 99, name: 'Южный' }])
    const trigger = screen.getByRole('button', { name: 'Сменить парк' })
    await actor.click(trigger)
    await actor.keyboard('юж')
    expect(screen.getByRole('option', { name: 'Южный' })).toHaveFocus()
    await actor.keyboard('{Tab}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).not.toHaveFocus()
  })

  it('keeps the operator report badge park-aware and refreshes it on demand and entry', async () => {
    const actor = userEvent.setup()
    const south = { id: 9, name: 'Южный', tag: 'south', is_active: true }
    renderApp('/overview?park=7', testUser({
      permissions: ['nav.dashboard', 'nav.reports'],
      parks: [north, south],
    }))
    const badge = vi.mocked(api.reportsBadge)

    await waitFor(() => expect(badge).toHaveBeenCalledWith(7))
    const beforeEvent = badge.mock.calls.length
    act(() => window.dispatchEvent(new Event(REPORTS_BADGE_REFRESH)))
    await waitFor(() => expect(badge.mock.calls.length).toBeGreaterThan(beforeEvent))

    await actor.click(screen.getByRole('button', { name: 'Сменить парк' }))
    await actor.click(screen.getByRole('option', { name: 'Южный' }))
    await waitFor(() => expect(badge).toHaveBeenCalledWith(9))

    const beforeReports = badge.mock.calls.length
    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    await actor.click(within(desktopNavigation).getByRole('link', { name: 'Репорты' }))
    await waitFor(() => expect(badge.mock.calls.length).toBeGreaterThan(beforeReports))
  })

  it('renders the royal report badge count returned for mixed-target inbox items', async () => {
    vi.mocked(api.reportsBadge).mockResolvedValue({ count: 4 })
    renderApp('/overview?park=7', testUser({
      role: 'royal',
      permissions: ['nav.dashboard', 'nav.reports'],
      parks: [north],
    }))

    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    const reportsLink = within(desktopNavigation).getByRole('link', { name: 'Репорты' })
    await waitFor(() => expect(within(reportsLink).getByText('4')).toBeVisible())
    expect(vi.mocked(api.reportsBadge).mock.calls).not.toContainEqual([7])
    expect(vi.mocked(api.reportsBadge).mock.calls).toContainEqual([undefined])
  })

  it.each(['operator', 'admin', 'royal'] as const)('switches parks from the %s park identity', async (role) => {
    const actor = userEvent.setup()
    const { setParkId } = renderShellWithParkScope(role)

    expect(screen.queryByText('РобоПарк')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Сменить парк' })).toHaveTextContent('Северный')
    await actor.click(screen.getByRole('button', { name: 'Сменить парк' }))
    const options = screen.getByRole('listbox', { name: 'Сменить парк' })
    expect(within(options).getByRole('option', { name: 'Северный' })).toHaveAttribute('aria-selected', 'true')
    await actor.click(within(options).getByRole('option', { name: 'Южный' }))

    expect(setParkId).toHaveBeenCalledWith(9)
  })

  it.each(['mechanic', 'driver'] as const)('shows a static park identity for %s', (role) => {
    renderShellWithParkScope(role)

    expect(screen.queryByRole('button', { name: 'Сменить парк' })).not.toBeInTheDocument()
    expect(screen.queryByText('РобоПарк')).not.toBeInTheDocument()
    expect(screen.getByText('Северный', { selector: 'strong' })).toBeVisible()
  })

  it.each(['operator', 'admin', 'royal'] as const)('keeps the %s park identity static with one park', (role) => {
    renderShellWithParkScope(role, vi.fn(), [north])

    expect(screen.queryByRole('button', { name: 'Сменить парк' })).not.toBeInTheDocument()
    expect(screen.getByText('Северный', { selector: 'strong' })).toBeVisible()
  })

  it('never commits a report badge count under a different operator park key', async () => {
    const snapshots: string[] = []
    const south = { id: 9, name: 'Южный', tag: 'south', is_active: true }
    let resolveSouth: ((value: { count: number }) => void) | undefined
    const southResponse = new Promise<{ count: number }>((resolve) => {
      resolveSouth = resolve
    })
    vi.mocked(api.reportsBadge).mockImplementation((parkId) => (
      parkId === 7 ? Promise.resolve({ count: 12 }) : southResponse
    ))

    const badgeUser = testUser({
      permissions: ['nav.dashboard', 'nav.reports'],
      parks: [north, south],
    })
    const tree = (parkId: number) => (
      <MemoryRouter initialEntries={['/overview']}>
        <ThemeProvider>
          <AuthContext.Provider value={{
            user: badgeUser,
            loading: false,
            login: vi.fn(),
            refreshUser: vi.fn(),
            logout: vi.fn(),
          }}>
            <ParkScopeContext.Provider value={{
              parkId,
              selectedPark: parkId === 7 ? north : south,
              parks: [north, south],
              loading: false,
              locked: false,
              setParkId: vi.fn(),
              refreshParks: vi.fn(),
            }}>
              <Routes>
                <Route element={<AppShell />}>
                  <Route path="/overview" element={<h1>Дашборд</h1>} />
                </Route>
              </Routes>
              <BadgeCommitProbe parkId={parkId} snapshots={snapshots} />
            </ParkScopeContext.Provider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>
    )
    const app = render(tree(7))

    expect(await screen.findByText('12')).toBeVisible()
    app.rerender(tree(9))

    expect(snapshots).not.toContain('9:12')
    expect(screen.queryByText('12')).not.toBeInTheDocument()

    await act(async () => resolveSouth?.({ count: 0 }))
  })

  it('releases a pending badge owner across a same-id identity change and fresh A remount', async () => {
    let resolveOld!: (value: { count: number }) => void
    const old = new Promise<{ count: number }>((resolve) => { resolveOld = resolve })
    vi.mocked(api.reportsBadge)
      .mockImplementationOnce(() => old)
      .mockResolvedValueOnce({ count: 0 })
      .mockResolvedValueOnce({ count: 3 })
    const first = testUser({
      id: 17,
      username: 'first-name',
      tracker_login: 'first.login',
      permissions: ['nav.reports'],
      parks: [north],
    })
    const changed = { ...first, username: 'changed-name' }
    const tree = (currentUser: typeof first) => (
      <MemoryRouter initialEntries={['/overview']}>
        <ThemeProvider>
          <AuthContext.Provider value={{
            user: currentUser,
            loading: false,
            login: vi.fn(),
            refreshUser: vi.fn(),
            logout: vi.fn(),
          }}>
            <ParkScopeContext.Provider value={{
              parkId: 7,
              selectedPark: north,
              parks: currentUser.parks,
              loading: false,
              locked: false,
              setParkId: vi.fn(),
              refreshParks: vi.fn(),
            }}>
              <Routes><Route element={<AppShell />}>
                <Route path="/overview" element={<h1>Дашборд</h1>} />
              </Route></Routes>
            </ParkScopeContext.Provider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>
    )

    const view = render(tree(first))
    await waitFor(() => expect(api.reportsBadge).toHaveBeenCalledTimes(1))
    view.rerender(tree(changed))
    await waitFor(() => expect(api.reportsBadge).toHaveBeenCalledTimes(2))
    view.rerender(tree(first))
    expect(await screen.findAllByText('3')).not.toHaveLength(0)
    expect(api.reportsBadge).toHaveBeenCalledTimes(3)

    await act(async () => resolveOld({ count: 12 }))
    expect(screen.queryByText('12')).not.toBeInTheDocument()
    expect(screen.getAllByText('3')).not.toHaveLength(0)
  })

  it.each([
    ['/admin/emergency/config', 'Настройка проверки робота'],
    ['/admin/emergency/config/sections', 'Настройка проверки робота'],
  ])('keeps the administration parent and nested destination active at %s', (path, destinationName) => {
    const adminUser = testUser({
      permissions: ['nav.admin', 'nav.admin.tracker', 'nav.admin.emergency'],
      parks: [north],
    })
    render(
      <MemoryRouter initialEntries={[path]}>
        <ThemeProvider>
          <AuthContext.Provider value={{
            user: adminUser,
            loading: false,
            login: vi.fn(),
            refreshUser: vi.fn(),
            logout: vi.fn(),
          }}>
            <ParkProvider>
              <Routes>
                <Route element={<AppShell />}>
                  <Route path="/admin/tracker/*" element={<h1>Startrek route</h1>} />
                  <Route path="/admin/emergency/config/*" element={<h1>Config route</h1>} />
                </Route>
              </Routes>
            </ParkProvider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>,
    )

    const navigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    const parent = within(navigation).getByRole('link', { name: 'Администрирование' })
    const destination = within(navigation).getByRole('link', { name: destinationName })
    expect(parent).toHaveClass('is-active')
    expect(parent).toHaveAttribute('aria-current', 'true')
    expect(destination).toHaveClass('is-active')
    expect(destination).toHaveAttribute('aria-current', 'page')
    expect(navigation.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
  })

  it.each(['royal', 'admin', 'operator', 'mechanic', 'driver'] as const)('keeps %s bottom navigation captions from collapsing in compact mode', role => {
    act(() => media.setWidth(320))
    localStorage.setItem('robopark-density', 'compact')
    const style = document.createElement('style')
    style.textContent = shellCss
    document.head.append(style)
    // JSDOM does not evaluate media queries; apply the active CSS rules using
    // the same viewport controller as the shell.
    const active = document.createElement('style')
    active.textContent = Array.from(style.sheet!.cssRules).flatMap(rule =>
      'conditionText' in rule && 'cssRules' in rule && matchMedia(rule.conditionText as string).matches
        ? Array.from((rule as CSSMediaRule).cssRules).map(child => child.cssText) : [],
    ).join('\n')
    document.head.append(active)
    try {
      renderShellPath('/robots', { ...operator, role })
      const captions = document.querySelectorAll('.rp-shell__bottom-nav .rp-shell__nav-label')
      expect(captions.length).toBeGreaterThan(1)
      for (const caption of captions) {
        expect(caption.textContent?.trim()).not.toBe('')
        expect(getComputedStyle(caption).flexShrink).toBe('0')
        expect(getComputedStyle(caption).flexBasis).toBe('auto')
        expect(getComputedStyle(caption).display).not.toBe('none')
      }
    } finally { active.remove(); style.remove() }
  })

  it('gives the skip link and Work summary action the shared minimum control size', async () => {
    const style = document.createElement('style')
    style.textContent = `${shellCss}\n${overviewCss}\n${workCss}`
    document.head.append(style)
    renderApp('/work', operator)

    const skipLink = screen.getByRole('link', { name: 'К содержанию' })
    const quickLink = await within(screen.getByRole('main')).findByRole('button', { name: 'Сводка смены' })
    expect(declaredCssValue(skipLink, 'min-height')).toBe('var(--rp-control-min-size)')
    expect(getComputedStyle(skipLink).display).toBe('inline-flex')
    expect(declaredCssValue(quickLink, 'min-height')).toBe('var(--rp-control-min-size)')
    style.remove()
  })

  it('falls back to the main landmark when a destination has no h1', async () => {
    const noHeadingUser = testUser({ permissions: ['nav.dashboard', 'nav.tasks'], parks: [north] })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <ThemeProvider>
          <AuthContext.Provider value={{
            user: noHeadingUser,
            loading: false,
            login: vi.fn(),
            refreshUser: vi.fn(),
            logout: vi.fn(),
          }}>
            <ParkProvider>
              <Routes>
                <Route element={<AppShell />}>
                  <Route path="/overview" element={<h1>Дашборд</h1>} />
                  <Route path="/work" element={<p>Рабочая область без заголовка</p>} />
                </Route>
              </Routes>
            </ParkProvider>
          </AuthContext.Provider>
        </ThemeProvider>
      </MemoryRouter>,
    )

    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    fireEvent.click(within(desktopNavigation).getByRole('link', { name: 'Работа' }))

    await waitFor(() => expect(screen.getByRole('main')).toHaveFocus())
  })
})
