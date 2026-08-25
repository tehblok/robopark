import type { ReactNode } from 'react'
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
}: {
  title?: string
  hint?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="panel">
      {(title || hint || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {hint && <p className="panel-hint">{hint}</p>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </div>
      )}
      {children}
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
