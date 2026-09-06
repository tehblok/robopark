import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react'
import { Link, Outlet, useLocation, useNavigationType } from 'react-router-dom'
import { api, type Park, type User } from '../../api'
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
import { reportsAccessIdentity } from '../../domains/reports/reports'
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

const PARK_SWITCH_ROLES = new Set<User['role']>(['operator', 'admin', 'royal'])

function ParkIdentity({
  user,
  parkId,
  selectedPark,
  parks,
  loading,
  locked,
  onChange,
  allowAllParks = false,
}: {
  user: User
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
  locked: boolean
  allowAllParks?: boolean
  onChange: (id: number | null) => void
}) {
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(0)
  const selectorId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const brandRef = useRef<HTMLDivElement>(null)
  const typed = useRef({ text: '', at: 0 })
  const parkOptions: { id: number | null; name: string }[] = allowAllParks && parks.length
    ? [{ id: null, name: 'Все доступные парки' }, ...parks] : parks
  const parkName = selectedPark?.name ?? (loading ? ru.loading : allowAllParks && parks.length ? 'Все доступные парки' : 'Без парка')
  const canSwitch = PARK_SWITCH_ROLES.has(user.role)
    && !locked
    && parkOptions.length > 1

  useLayoutEffect(() => {
    if (selectorOpen) listRef.current?.querySelectorAll('button')[focusedIndex]?.focus()
  }, [selectorOpen, focusedIndex])

  useEffect(() => {
    if (!selectorOpen) return
    const dismissOutside = (event: PointerEvent) => {
      if (!brandRef.current?.contains(event.target as Node)) setSelectorOpen(false)
    }
    document.addEventListener('pointerdown', dismissOutside)
    return () => document.removeEventListener('pointerdown', dismissOutside)
  }, [selectorOpen])

  const openSelector = () => {
    typed.current = { text: '', at: 0 }
    setFocusedIndex(Math.max(0, parkOptions.findIndex((park) => park.id === parkId)))
    setSelectorOpen(true)
  }

  const closeSelector = () => {
    setSelectorOpen(false)
    triggerRef.current?.focus()
  }

  const navigateOptions = (event: KeyboardEvent<HTMLDivElement>) => {
    const { key } = event
    if (key === 'Escape') {
      event.preventDefault()
      closeSelector()
    } else if (key === 'Tab') {
      // Let the browser continue its normal tab order from the trigger.
      closeSelector()
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) {
      event.preventDefault()
      typed.current = { text: '', at: 0 }
      setFocusedIndex(key === 'Home' ? 0 : key === 'End' ? parkOptions.length - 1
        : Math.max(0, Math.min(parkOptions.length - 1, focusedIndex + (key === 'ArrowDown' ? 1 : -1))))
    } else if (key.length === 1 && key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault()
      const now = Date.now()
      const text = (now - typed.current.at < 1000 ? typed.current.text : '') + key.toLocaleLowerCase('ru-RU')
      typed.current = { text, at: now }
      const prefix = [...text].every((letter) => letter === text[0]) ? text[0] : text
      const start = prefix.length === 1 ? focusedIndex + 1 : focusedIndex
      for (let offset = 0; offset < parkOptions.length; offset += 1) {
        const index = (start + offset) % parkOptions.length
        if (parkOptions[index].name.toLocaleLowerCase('ru-RU').startsWith(prefix)) {
          setFocusedIndex(index)
          break
        }
      }
    }
  }

  return (
    <div
      className={`rp-shell__park-brand${canSwitch ? ' is-interactive' : ''}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setSelectorOpen(false)
      }}
      ref={brandRef}
    >
      {canSwitch ? (
        <>
          <button
            aria-controls={selectorOpen ? selectorId : undefined}
            aria-expanded={selectorOpen}
            aria-haspopup="listbox"
            aria-label="Сменить парк"
            className="rp-shell__park-switch"
            onClick={() => selectorOpen ? closeSelector() : openSelector()}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault()
                openSelector()
              }
            }}
            ref={triggerRef}
            type="button"
          >
            <strong className="rp-shell__park-brand-name">{parkName}</strong>
            <span aria-hidden="true" className="rp-shell__park-brand-chevron" />
          </button>
          {selectorOpen ? (
            <div
              aria-label="Сменить парк"
              aria-description={parks.length > 7 ? 'Начните вводить название для поиска парка.' : undefined}
              className="rp-shell__park-selector"
              id={selectorId}
              onKeyDown={navigateOptions}
              ref={listRef}
              role="listbox"
            >
            {parkOptions.map((park, index) => (
                <button
                  aria-selected={park.id === parkId}
                  key={park.id ?? 'all'}
                  onClick={() => {
                    onChange(park.id)
                    closeSelector()
                  }}
                  onFocus={() => setFocusedIndex(index)}
                  role="option"
                  tabIndex={focusedIndex === index ? 0 : -1}
                  type="button"
                >
                  {park.name}
                </button>
            ))}
            </div>
          ) : null}
        </>
      ) : <strong className="rp-shell__park-brand-name">{parkName}</strong>}
    </div>
  )
}

function currentNavigationItem(items: readonly NavigationItem[], pathname: string) {
  return items.reduce<NavigationItem | undefined>((current, item) => {
    const matches = pathname === item.path || pathname.startsWith(`${item.path}/`)
    return matches && (!current || item.path.length > current.path.length) ? item : current
  }, undefined)
}

function NavigationLink({
  item,
  reportsBadge,
  className,
  active,
  onClick,
}: {
  item: NavigationItem
  reportsBadge: number
  className: string
  active: boolean
  onClick?: () => void
}) {
  const label = item.id === 'work' ? ru.appShell.work : item.label
  const { pathname } = useLocation()
  const parentActive = item.id === 'admin' && pathname.startsWith('/admin/') && !active
  return (
    <Link
      aria-label={label}
      aria-current={active ? 'page' : parentActive ? 'true' : undefined}
      className={`${className}${active || parentActive ? ' is-active' : ''}`}
      data-route-id={item.id}
      onClick={onClick}
      to={item.path}
    >
      <Icon name={item.icon} size={20} />
      <span className="rp-shell__nav-label">{label}</span>
      {item.id === 'reports' ? <ReportsBadge count={reportsBadge} /> : null}
    </Link>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const { parkId, selectedPark, parks, loading, locked, setParkId, allowAllParks } = useParkScope()
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
  const desktopCurrent = currentNavigationItem(desktopItems, location.pathname)
  const mobileCurrent = currentNavigationItem(mobileItems, location.pathname)
  const moreCurrent = secondaryMobileItems.some((item) => item.id === mobileCurrent?.id)
  const badgeParkId = parkId ?? undefined
  const badgeIdentity = user ? reportsAccessIdentity(user, selectedPark) : ''
  const badgeKey = user ? `reports:badge:${user.id}:${badgeIdentity}` : ''
  const committedBadgeKey = useRef(badgeKey)
  const [visibleBadgeKey, setVisibleBadgeKey] = useState(badgeKey)
  const badgeResource = useCachedResource(
    badgeKey,
    () => api.reportsBadge(badgeParkId),
    { enabled: Boolean(user), persist: false },
  )
  const reportsBadge = user && visibleBadgeKey === badgeKey
    ? badgeResource.data?.count ?? 0
    : 0
  const refreshBadge = badgeResource.refresh

  useLayoutEffect(() => {
    if (committedBadgeKey.current === badgeKey) return
    if (committedBadgeKey.current) resourceStore.invalidate(committedBadgeKey.current)
    committedBadgeKey.current = badgeKey
    setVisibleBadgeKey(badgeKey)
  }, [badgeKey])

  useLayoutEffect(() => () => {
    if (committedBadgeKey.current) resourceStore.invalidate(committedBadgeKey.current)
  }, [])

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
        {!phoneViewport ? (
          <ParkIdentity
            allowAllParks={allowAllParks}
            loading={loading}
            locked={locked}
            onChange={setParkId}
            parkId={parkId}
            parks={parks}
            selectedPark={selectedPark}
            user={user}
          />
        ) : null}
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
                    active={item.id === desktopCurrent?.id}
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
          {phoneViewport ? (
            <ParkIdentity
              allowAllParks={allowAllParks}
              loading={loading}
              locked={locked}
              onChange={setParkId}
              parkId={parkId}
              parks={parks}
              selectedPark={selectedPark}
              user={user}
            />
          ) : null}
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
                active={item.id === mobileCurrent?.id}
                className="rp-shell__mobile-link"
                item={item}
                key={item.id}
                reportsBadge={reportsBadge}
              />
            ))}
            <button
              aria-current={moreCurrent ? 'page' : undefined}
              aria-expanded={moreOpen}
              className={`rp-shell__mobile-link${moreOpen || moreCurrent ? ' is-active' : ''}`}
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
                active={item.id === mobileCurrent?.id}
                className="rp-shell__more-link"
                item={item}
                key={item.id}
                onClick={() => setMoreOpen(false)}
                reportsBadge={reportsBadge}
              />
            ))}
          </nav>
        ) : null}

        <nav aria-label="Профиль" className="rp-shell__more-nav">
          <Link className="rp-shell__more-link" onClick={() => setMoreOpen(false)} to="/change-password">
            <Icon name="settings" size={20} />
            <span className="rp-shell__nav-label">Сменить пароль</span>
          </Link>
        </nav>

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
