import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ru } from '../i18n/ru'

type PageShellProps = {
  title: string
  subtitle?: string
  backTo?: string
  backLabel?: string
  /** Toolbar rendered on the right of the title (refresh, create, …). */
  actions?: ReactNode
  /** Wrap in centered .page layout (pages outside AppShell) */
  standalone?: boolean
  /** Logout for pages rendered outside AppShell */
  onLogout?: () => void
  children: ReactNode
}

export function PageShell({
  title,
  subtitle,
  backTo,
  backLabel = ru.back,
  actions,
  standalone = false,
  onLogout,
  children,
}: PageShellProps) {
  const content = (
    <>
      <header className="page-header">
        {backTo && (
          <Link className="page-back" to={backTo}>
            ← {backLabel}
          </Link>
        )}
        <div className="page-header-row">
          <div className="page-header-text">
            <h1>{title}</h1>
            {subtitle && <p className="page-subtitle">{subtitle}</p>}
          </div>
          <div className="page-header-actions">
            {actions}
            {onLogout && (
              <button className="btn btn-secondary" onClick={onLogout} type="button">
                {ru.signOut}
              </button>
            )}
          </div>
        </div>
      </header>
      <div className="page-body">{children}</div>
    </>
  )

  if (standalone) {
    return (
      <main className="page">
        <div className="page-content shell animate-in">{content}</div>
      </main>
    )
  }

  return <div className="page-content animate-in">{content}</div>
}

export function Panel({
  title,
  hint,
  actions,
  children,
  collapsible = false,
  storageKey,
  defaultCollapsed = false,
}: {
  title?: string
  hint?: string
  actions?: ReactNode
  children: ReactNode
  collapsible?: boolean
  storageKey?: string
  defaultCollapsed?: boolean
}) {
  const contentId = useId()
  if (import.meta.env.DEV && collapsible && (!title?.trim() || !storageKey?.trim())) {
    throw new Error('A collapsible Panel requires a nonempty title and storageKey.')
  }
  const [collapsed, setCollapsed] = useState(() => {
    if (!storageKey || typeof window === 'undefined') return defaultCollapsed
    try {
      return window.localStorage.getItem(`robopark:panel:${storageKey}:collapsed`) === '1'
    } catch {
      return defaultCollapsed
    }
  })
  const canCollapse = collapsible && Boolean(title?.trim() && storageKey?.trim())
  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    try {
      if (storageKey) {
        const key = `robopark:panel:${storageKey}:collapsed`
        if (next) window.localStorage.setItem(key, '1')
        else window.localStorage.removeItem(key)
      }
    } catch {
      // Persistent storage can be unavailable in private or embedded contexts.
    }
  }
  return (
    <section className={`panel${canCollapse && collapsed ? ' panel-collapsed' : ''}`}>
      {(title || hint || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {hint && <p className="panel-hint">{hint}</p>}
          </div>
          {(actions || canCollapse) && <div className="panel-actions">
            {canCollapse && <button
              aria-label={collapsed ? `Развернуть: ${title}` : `Свернуть: ${title}`}
              aria-controls={collapsed ? undefined : contentId}
              aria-expanded={!collapsed}
              className="panel-collapse"
              onClick={toggleCollapsed}
              type="button"
            >
              {collapsed ? 'Развернуть' : 'Свернуть'}
            </button>}
            {actions}
          </div>}
        </div>
      )}
      {!collapsed && <div className="panel-body" id={contentId}>{children}</div>}
    </section>
  )
}

export function Badge({ active }: { active: boolean }) {
  return (
    <span className={`badge ${active ? 'badge-ok' : 'badge-muted'}`}>
      {active ? ru.active : ru.inactive}
    </span>
  )
}

export function Alert({
  tone,
  children,
}: {
  tone: 'error' | 'success' | 'info' | 'warning'
  children: ReactNode
}) {
  const icons: Record<string, string> = {
    error: '⚠',
    success: '✓',
    info: 'i',
    warning: '!',
  }
  return (
    <p className={`alert alert-${tone}`} role={tone === 'error' ? 'alert' : undefined}>
      <span aria-hidden="true" className="alert-icon">
        {icons[tone]}
      </span>
      <span>{children}</span>
    </p>
  )
}
