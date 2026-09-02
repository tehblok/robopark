import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { AuthContext } from '../../auth-context'
import { ThemeProvider } from '../../design-system/theme/ThemeProvider'
import { ParkProvider } from '../../ParkProvider'
import {
  installMatchMedia,
  renderApp,
  testUser,
  type MatchMediaController,
} from '../../test/renderApp'
import { AppShell } from './AppShell'
import { REPORTS_BADGE_REFRESH } from '../../reports-badge'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }
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

describe('AppShell', () => {
  let media: MatchMediaController

  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    media = installMatchMedia({ width: 1200 })
    vi.spyOn(api, 'reportsBadge').mockResolvedValue({ count: 0 })
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

    const heading = await screen.findByRole('heading', { level: 1, name: 'Блокеры' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(desktopWork).not.toHaveFocus()
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
