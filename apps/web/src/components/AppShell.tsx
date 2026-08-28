import { type ReactNode, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { ru, roleLabel } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'
import {
  moreNavItemsFromPermissions,
  navItemsForPermissions,
  primaryNavItemsFromPermissions,
} from '../nav-permissions'
import { useParkContext } from '../park-context'
import { REPORTS_BADGE_REFRESH } from '../reports-badge'
import { getStoredTheme, setTheme, type Theme } from '../theme'

type AppShellProps = {
  children?: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { user, logout } = useAuth()
  const { parkId, setParkId, parks, parksLoading, parkLocked } = useParkContext()
  const [theme, setThemeState] = useState<Theme>(() => getStoredTheme())
  const [moreOpen, setMoreOpen] = useState(false)
  const location = useLocation()
  const badgeParkId = user?.role === 'operator' ? parkId ?? undefined : undefined
  const badgeRes = useCachedResource(
    user ? `reports:badge:${user.role}:${badgeParkId ?? 'all'}` : '',
    () => api.reportsBadge(badgeParkId),
    { enabled: Boolean(user), persist: false },
  )
  const reportsBadge = badgeRes.data?.count ?? 0

  useEffect(() => {
    if (!location.pathname.startsWith('/reports')) return
    void badgeRes.refresh()
  }, [location.pathname, badgeRes.refresh])

  useEffect(() => {
    const onRefresh = () => {
      void badgeRes.refresh()
    }
    window.addEventListener(REPORTS_BADGE_REFRESH, onRefresh)
    return () => window.removeEventListener(REPORTS_BADGE_REFRESH, onRefresh)
  }, [badgeRes.refresh])

  useEffect(() => {
    if (!moreOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMoreOpen(false)
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [moreOpen])

  useEffect(() => {
    setMoreOpen(false)
  }, [location.pathname])

  if (!user) return null

  const permissions = user.permissions ?? []
  const items = navItemsForPermissions(permissions)
  const primary = primaryNavItemsFromPermissions(permissions)
  const more = moreNavItemsFromPermissions(permissions)
  const showAdminLink = permissions.includes('nav.admin')
  const selectedPark = parks.find((park) => park.id === parkId)
  const needsParkSelector =
    permissions.includes('nav.dashboard') ||
    permissions.includes('nav.tasks') ||
    permissions.includes('nav.reports') ||
    permissions.includes('nav.analytics')

  const toggleTheme = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light'
    setTheme(next)
    setThemeState(next)
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark-grid" aria-hidden>
            <span />
            <span />
            <span />
            <span />
          </span>
          <span className="sidebar-brand-text">{ru.nav.brand}</span>
        </div>
        <nav className="sidebar-nav">
          {items.map((item) => (
            <NavLink
              key={item.id}
              to={item.path}
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              <span aria-hidden="true" className="nav-item-icon">
                {item.icon}
              </span>
              <span className="nav-item-label">{item.label}</span>
              {item.id === 'reports' && reportsBadge > 0 ? (
                <span className="nav-count">{reportsBadge}</span>
              ) : null}
              {item.stub ? <span className="nav-soon">{ru.nav.soon}</span> : null}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-leading">
            {(needsParkSelector && (parks.length > 0 || parksLoading)) && (
              <div className="topbar-park">
                <span className="topbar-field-label">{ru.nav.park}</span>
                {parkLocked ? (
                  <span className="topbar-pill topbar-park-value">
                    {selectedPark?.name ?? (parksLoading ? ru.loading : '—')}
                  </span>
                ) : (
                  <select
                    className="topbar-pill topbar-select"
                    value={parkId ?? ''}
                    disabled={parksLoading || parks.length === 0}
                    onChange={(event) => setParkId(Number(event.target.value))}
                  >
                    {parks.length === 0 ? (
                      <option value="">{parksLoading ? ru.loading : '—'}</option>
                    ) : (
                      parks.map((park) => (
                        <option key={park.id} value={park.id}>
                          {park.name}
                        </option>
                      ))
                    )}
                  </select>
                )}
              </div>
            )}
          </div>
          <button
            type="button"
            className="topbar-pill topbar-user mobile-user-open"
            onClick={() => setMoreOpen(true)}
          >
            <span className="topbar-user-name">{user.username}</span>
            <span className="topbar-user-role">{roleLabel(user.role)}</span>
          </button>
          <details className="topbar-menu">
            <summary className="topbar-pill topbar-user">
              <span className="topbar-user-name">{user.username}</span>
              <span className="topbar-user-role">{roleLabel(user.role)}</span>
            </summary>
            <div className="topbar-menu-panel">
              <button type="button" className="topbar-menu-item" onClick={toggleTheme}>
                {theme === 'light' ? ru.theme.dark : ru.theme.light}
              </button>
              {showAdminLink && (
                <Link className="topbar-menu-item" to="/admin">
                  {ru.nav.admin}
                </Link>
              )}
              <button
                type="button"
                className="topbar-menu-item"
                onClick={() => {
                  void logout()
                }}
              >
                {ru.signOut}
              </button>
            </div>
          </details>
        </header>
        <div className="app-content">{children ?? <Outlet />}</div>

        <nav className="mobile-bottom-nav" aria-label={ru.nav.brand}>
          {primary.map((item) => (
            <NavLink
              key={item.id}
              to={item.path}
              className={({ isActive }) =>
                isActive ? 'mobile-nav-item active' : 'mobile-nav-item'
              }
            >
              <span aria-hidden="true" className="mobile-nav-icon">
                {item.icon}
              </span>
              <span className="mobile-nav-label">{item.label}</span>
              {item.id === 'reports' && reportsBadge > 0 ? (
                <span className="nav-count">{reportsBadge}</span>
              ) : null}
            </NavLink>
          ))}
          <button
            type="button"
            className={moreOpen ? 'mobile-nav-item active' : 'mobile-nav-item'}
            aria-expanded={moreOpen}
            aria-controls="mobile-more-sheet"
            onClick={() => setMoreOpen((v) => !v)}
          >
            <span aria-hidden="true" className="mobile-nav-icon">
              ⋯
            </span>
            <span className="mobile-nav-label">{ru.nav.more}</span>
          </button>
        </nav>

        {moreOpen ? (
          <>
            <button
              type="button"
              className="mobile-more-backdrop"
              aria-label={ru.nav.close}
              onClick={() => setMoreOpen(false)}
            />
            <div
              id="mobile-more-sheet"
              className="mobile-more-sheet"
              role="dialog"
              aria-modal="true"
              aria-label={ru.nav.more}
            >
              <div className="mobile-more-head">
                <strong>{user.username}</strong>
                <span className="topbar-user-role">{roleLabel(user.role)}</span>
                <button type="button" className="btn-ghost" onClick={() => setMoreOpen(false)}>
                  {ru.nav.close}
                </button>
              </div>
              {more.length > 0 ? (
              <nav className="mobile-more-nav">
                {more.map((item) => (
                  <NavLink
                    key={item.id}
                    to={item.path}
                    className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
                    onClick={() => setMoreOpen(false)}
                  >
                    <span aria-hidden="true" className="nav-item-icon">
                      {item.icon}
                    </span>
                    <span className="nav-item-label">{item.label}</span>
                    {item.stub ? <span className="nav-soon">{ru.nav.soon}</span> : null}
                  </NavLink>
                ))}
              </nav>
              ) : null}
              <div className="mobile-more-actions">
                {showAdminLink ? (
                  <Link className="topbar-menu-item" to="/admin" onClick={() => setMoreOpen(false)}>
                    {ru.nav.admin}
                  </Link>
                ) : null}
                <button type="button" className="topbar-menu-item" onClick={toggleTheme}>
                  {theme === 'light' ? ru.theme.dark : ru.theme.light}
                </button>
                <button
                  type="button"
                  className="topbar-menu-item"
                  onClick={() => {
                    setMoreOpen(false)
                    void logout()
                  }}
                >
                  {ru.signOut}
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
