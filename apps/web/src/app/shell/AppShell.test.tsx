import { useLayoutEffect } from 'react'
import { readFileSync } from 'node:fs'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
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

const shellCss = readFileSync('src/app/shell/AppShell.css', 'utf8')
const overviewCss = readFileSync('src/domains/shift/overview.css', 'utf8')

const north = { id: 7, name: 'Северный', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
const operator = testUser({
  permissions: [
    'nav.dashboard',
    'nav.tasks',
    'nav.robot_search',
    'nav.emergency',
    'nav.reports',
    'nav.analytics',
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
  let media: MatchMediaController

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    media = installMatchMedia({ width: 1200 })
    vi.spyOn(api, 'reportsBadge').mockResolvedValue({ count: 0 })
    vi.spyOn(api, 'dashboardSummary').mockResolvedValue({ park_id: 7, generated_at: '2026-09-02T09:00:00Z', arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [] })
    vi.spyOn(api, 'operatorBlockers').mockResolvedValue({ park_id: 7, park_tag: 'north', status: 'all', counts: {}, items: [] })
    vi.spyOn(api, 'trackerIssues').mockResolvedValue({ items: [], total: 0, limit: 30, offset: 0, has_more: false })
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

    const park = await screen.findByRole('combobox', { name: 'Парк' })
    await actor.selectOptions(park, '9')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?park=9'))
    expect(park).toHaveFocus()
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

    await actor.selectOptions(screen.getByRole('combobox', { name: 'Парк' }), '9')
    await waitFor(() => expect(badge).toHaveBeenCalledWith(9))

    const beforeReports = badge.mock.calls.length
    const desktopNavigation = screen.getAllByRole('navigation', {
      name: 'Основная навигация',
    })[0]
    await actor.click(within(desktopNavigation).getByRole('link', { name: 'Репорты' }))
    await waitFor(() => expect(badge.mock.calls.length).toBeGreaterThan(beforeReports))
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

  it.each([
    ['/admin/tracker', 'Startrek'],
    ['/admin/emergency/config', 'Настройка проверки робота'],
  ])('marks only the exact nested destination active at %s', (path, destinationName) => {
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
                  <Route path="/admin/tracker" element={<h1>Startrek route</h1>} />
                  <Route path="/admin/emergency/config" element={<h1>Config route</h1>} />
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
    expect(parent).not.toHaveClass('is-active')
    expect(parent).not.toHaveAttribute('aria-current')
    expect(destination).toHaveClass('is-active')
    expect(destination).toHaveAttribute('aria-current', 'page')
  })

  it('gives the skip link and Overview primary action the shared minimum control size', async () => {
    const style = document.createElement('style')
    style.textContent = `${shellCss}\n${overviewCss}`
    document.head.append(style)
    renderApp('/overview', operator)

    const skipLink = screen.getByRole('link', { name: 'К содержанию' })
    const quickLink = await within(screen.getByRole('main')).findByRole('link', { name: 'Открыть работу' })
    expect(declaredCssValue(skipLink, 'min-height')).toBe('var(--rp-control-min-size)')
    expect(getComputedStyle(skipLink).display).toBe('inline-flex')
    expect(declaredCssValue(quickLink, 'min-height')).toBe('var(--rp-control-min-size)')
    expect(getComputedStyle(quickLink).display).toBe('flex')
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
