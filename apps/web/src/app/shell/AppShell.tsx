import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { NavLink, Outlet, useLocation, useNavigationType } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth-context'
import { Button, IconButton } from '../../design-system/actions/Button'
import { Icon } from '../../design-system/icons/Icon'
import { BottomSheet } from '../../design-system/overlays/BottomSheet'
import { useTheme } from '../../design-system/theme/ThemeProvider'
import { DENSITY_MEDIA_QUERY } from '../../design-system/theme/theme'
import { ru, roleLabel } from '../../i18n/ru'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { useParkScope } from '../park/parkScope'
import { navigationForUser } from '../routing/accessPolicy'
import type { NavigationItem, NavGroup } from '../routing/routeManifest'
import { REPORTS_BADGE_REFRESH } from '../../reports-badge'
import './AppShell.css'

const GROUPS: readonly NavGroup[] = [
  'operations',
  'collaboration',
  'insights',
  'administration',
]

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => matchMedia(query).matches)

  useEffect(() => {
    const media = matchMedia(query)
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    setMatches(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [query])

  return matches
}

function ReportsBadge({ count }: { count: number }) {
  return count > 0 ? <span className="rp-shell__badge">{count}</span> : null
}

function NavigationLink({
  item,
  reportsBadge,
  className,
  onClick,
}: {
  item: NavigationItem
  reportsBadge: number
  className: string
  onClick?: () => void
}) {
  const label = item.id === 'work' ? ru.appShell.work : item.label
  return (
    <NavLink
      className={({ isActive }) => `${className}${isActive ? ' is-active' : ''}`}
      data-route-id={item.id}
      end
      onClick={onClick}
      to={item.path}
    >
      <Icon name={item.icon} size={20} />
      <span className="rp-shell__nav-label">{label}</span>
      {item.id === 'reports' ? <ReportsBadge count={reportsBadge} /> : null}
    </NavLink>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { parkId, selectedPark, parks, loading, locked, setParkId } = useParkScope()
  const {
    preference,
    resolvedTheme,
    setPreference,
    densityPreference,
    resolvedDensity,
    setDensityPreference,
  } = useTheme()
  const location = useLocation()
  const navigationType = useNavigationType()
  const navigationTypeRef = useRef(navigationType)
  const previousPathname = useRef(location.pathname)
  const [moreOpen, setMoreOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const phoneViewport = useMediaQuery(DENSITY_MEDIA_QUERY)
  const splitTablet = useMediaQuery('(min-width: 900px) and (max-width: 1199px)')

  const desktopItems = useMemo(
    () => user ? navigationForUser(user, 'desktop') : [],
    [user],
  )
  const mobileItems = useMemo(
    () => user ? navigationForUser(user, 'mobile') : [],
    [user],
  )
  const primaryMobileItems = mobileItems.slice(0, 4)
  const secondaryMobileItems = mobileItems.slice(4)
  const badgeParkId = user?.role === 'operator' ? parkId ?? undefined : undefined
  const badgeKey = user ? `reports:badge:${user.role}:${badgeParkId ?? 'all'}` : ''
  const badgeResource = useCachedResource(
    badgeKey,
    () => api.reportsBadge(badgeParkId),
    { enabled: Boolean(user), persist: false },
  )
  const reportsBadge = user
    ? resourceStore.get<{ count: number }>(badgeKey)?.count ?? 0
    : 0
  const refreshBadge = badgeResource.refresh

  useLayoutEffect(() => {
    navigationTypeRef.current = navigationType
  }, [navigationType])

  useEffect(() => {
    if (location.pathname.startsWith('/reports')) void refreshBadge()
  }, [location.pathname, refreshBadge])

  useEffect(() => {
    const onRefresh = () => void refreshBadge()
    window.addEventListener(REPORTS_BADGE_REFRESH, onRefresh)
    return () => window.removeEventListener(REPORTS_BADGE_REFRESH, onRefresh)
  }, [refreshBadge])

  useEffect(() => {
    if (previousPathname.current === location.pathname) return
    previousPathname.current = location.pathname
    setMoreOpen(false)
    const focusNavigationType = navigationTypeRef.current

    const timer = window.setTimeout(() => {
      const main = document.querySelector<HTMLElement>('#main-content')
      if (!main) return
      const heading = main.querySelector<HTMLElement>('h1')
      const target = heading ?? main
      const addedTabIndex = heading != null && !heading.hasAttribute('tabindex')
      if (addedTabIndex) heading.setAttribute('tabindex', '-1')
      if (focusNavigationType === 'POP') target.focus({ preventScroll: true })
      else target.focus()
      if (addedTabIndex) {
        heading.addEventListener('blur', () => heading.removeAttribute('tabindex'), { once: true })
      }
    }, 0)

    return () => window.clearTimeout(timer)
  }, [location.pathname])

  useEffect(() => {
    if (!phoneViewport) return
    const activeElement = document.activeElement
    if (!(activeElement instanceof HTMLElement)) return
    const desktopLink = activeElement.closest<HTMLElement>('.rp-shell__desktop-link')
    if (!desktopLink) return

    const routeId = desktopLink.dataset.routeId
    const mobileTarget = routeId
      ? document.querySelector<HTMLElement>(
          `.rp-shell__bottom-nav [data-route-id="${routeId}"]`,
        )
      : null
    const focusTarget = mobileTarget ?? document.querySelector<HTMLElement>('#main-content')
    focusTarget?.focus()
  }, [phoneViewport])

  if (!user) return null

  const groupLabels = ru.appShell.groups
  const focusMain = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()
    document.querySelector<HTMLElement>('#main-content')?.focus()
  }

  return (
    <div
      className={`app-shell rp-app-shell${railCollapsed ? ' is-rail-collapsed' : ''}`}
      data-density={resolvedDensity}
      data-theme={resolvedTheme}
    >
      <a className="rp-shell__skip-link" href="#main-content" onClick={focusMain}>
        {ru.appShell.skipToContent}
      </a>

      <aside className="sidebar rp-shell__sidebar">
        <div className="rp-shell__brand">
          <Icon name="robot" size={24} />
          <span className="rp-shell__brand-label">{ru.nav.brand}</span>
        </div>
        {splitTablet ? (
          <IconButton
            className="rp-shell__rail-toggle"
            icon={railCollapsed ? 'forward' : 'back'}
            label={railCollapsed ? ru.appShell.expandNavigation : ru.appShell.collapseNavigation}
            onClick={() => setRailCollapsed((collapsed) => !collapsed)}
            variant="ghost"
          />
        ) : null}
        <nav aria-label={ru.appShell.mainNavigation} className="rp-shell__desktop-nav">
          {GROUPS.map((group) => {
            const items = desktopItems.filter((item) => item.group === group)
            if (items.length === 0) return null
            return (
              <section className="rp-shell__nav-group" key={group}>
                <h2>{groupLabels[group]}</h2>
                {items.map((item) => (
                  <NavigationLink
                    className="rp-shell__desktop-link"
                    item={item}
                    key={item.id}
                    reportsBadge={reportsBadge}
                  />
                ))}
              </section>
            )
          })}
        </nav>
      </aside>

      <div className="app-main rp-shell__main-column">
        <header className="rp-shell__topbar">
          <div className="rp-shell__park-context">
            {parks.length > 0 || loading ? (
              <label>
                <span>{ru.nav.park}</span>
                {locked ? (
                  <strong>{selectedPark?.name ?? (loading ? ru.loading : '—')}</strong>
                ) : (
                  <select
                    aria-label={ru.nav.park}
                    disabled={loading || parks.length === 0}
                    onChange={(event) => setParkId(Number(event.target.value))}
                    value={parkId ?? ''}
                  >
                    {parks.length === 0 ? <option value="">{ru.loading}</option> : null}
                    {parks.map((park) => (
                      <option key={park.id} value={park.id}>{park.name}</option>
                    ))}
                  </select>
                )}
              </label>
            ) : null}
          </div>
          <div className="rp-shell__topbar-actions">
            <span className="rp-shell__user">
              <strong>{user.username}</strong>
              <span>{roleLabel(user.role)}</span>
            </span>
            {!phoneViewport ? (
              <Button
                leadingIcon="more"
                onClick={() => setMoreOpen(true)}
                variant="ghost"
              >
                {ru.nav.more}
              </Button>
            ) : null}
          </div>
        </header>

        <main className="app-content rp-shell__content" id="main-content" tabIndex={-1}>
          <Outlet />
        </main>

        {phoneViewport ? (
          <nav aria-label={ru.appShell.mainNavigation} className="rp-shell__bottom-nav">
            {primaryMobileItems.map((item) => (
              <NavigationLink
                className="rp-shell__mobile-link"
                item={item}
                key={item.id}
                reportsBadge={reportsBadge}
              />
            ))}
            <button
              aria-expanded={moreOpen}
              className={`rp-shell__mobile-link${moreOpen ? ' is-active' : ''}`}
              onClick={() => setMoreOpen(true)}
              type="button"
            >
              <Icon name="more" size={20} />
              <span className="rp-shell__nav-label">{ru.nav.more}</span>
            </button>
          </nav>
        ) : null}
      </div>

      <BottomSheet
        description={`${user.username} · ${roleLabel(user.role)}`}
        onOpenChange={setMoreOpen}
        open={moreOpen}
        title={ru.nav.more}
      >
        {secondaryMobileItems.length > 0 ? (
          <nav aria-label={ru.appShell.secondaryNavigation} className="rp-shell__more-nav">
            {secondaryMobileItems.map((item) => (
              <NavigationLink
                className="rp-shell__more-link"
                item={item}
                key={item.id}
                onClick={() => setMoreOpen(false)}
                reportsBadge={reportsBadge}
              />
            ))}
          </nav>
        ) : null}

        <fieldset className="rp-shell__preference-group" role="radiogroup">
          <legend>{ru.appShell.themeLabel}</legend>
          {([
            ['system', ru.appShell.themeSystem],
            ['light', ru.appShell.themeLight],
            ['dark', ru.appShell.themeDark],
          ] as const).map(([value, label]) => (
            <label key={value}>
              <input
                checked={preference === value}
                name="rp-theme"
                onChange={() => setPreference(value)}
                type="radio"
                value={value}
              />
              {label}
            </label>
          ))}
        </fieldset>

        <fieldset className="rp-shell__preference-group" role="radiogroup">
          <legend>{ru.appShell.densityLabel}</legend>
          {([
            ['comfortable', ru.appShell.densityComfortable],
            ['compact', ru.appShell.densityCompact],
          ] as const).map(([value, label]) => (
            <label key={value}>
              <input
                checked={densityPreference === value}
                disabled={phoneViewport}
                name="rp-density"
                onChange={() => setDensityPreference(value)}
                type="radio"
                value={value}
              />
              {label}
            </label>
          ))}
          {phoneViewport ? <p>{ru.appShell.phoneDensity}</p> : null}
        </fieldset>

        <Button leadingIcon="logout" onClick={() => void logout()} variant="secondary">
          {ru.signOut}
        </Button>
      </BottomSheet>
    </div>
  )
}
