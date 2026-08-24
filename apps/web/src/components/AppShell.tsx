import { type ReactNode, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { ru, roleLabel } from '../i18n/ru'
import { navItemsForRole } from '../nav'
import { useParkContext } from '../park-context'
import { getStoredTheme, setTheme, type Theme } from '../theme'

type AppShellProps = {
  children?: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const { user, logout } = useAuth()
  const { parkId, setParkId, parks, parksLoading, parkLocked } = useParkContext()
  const [theme, setThemeState] = useState<Theme>(() => getStoredTheme())

  if (!user) return null

  const items = navItemsForRole(user.role)
  const showAdminLink = user.role === 'admin' || user.role === 'royal'
  const selectedPark = parks.find((park) => park.id === parkId)

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
              <span className="nav-item-label">{item.label}</span>
              {item.stub ? <span className="nav-soon">{ru.nav.soon}</span> : null}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-park">
            <span className="topbar-field-label">{ru.nav.park}</span>
            {parkLocked ? (
              <span className="topbar-pill topbar-park-value">
                {selectedPark?.name ?? '—'}
              </span>
            ) : (
              <select
                className="topbar-pill topbar-select"
                value={parkId ?? ''}
                disabled={parksLoading || parks.length === 0}
                onChange={(event) => setParkId(Number(event.target.value))}
              >
                {parks.length === 0 ? (
                  <option value="">—</option>
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
        <div className="app-content">{children}</div>
      </div>
    </div>
  )
}
