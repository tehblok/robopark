import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ru } from '../i18n/ru'

type PageShellProps = {
  title: string
  subtitle?: string
  backTo?: string
  backLabel?: string
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
          <div>
            <h1>{title}</h1>
            {subtitle && <p className="page-subtitle">{subtitle}</p>}
          </div>
          {onLogout && (
            <button className="btn btn-secondary" onClick={onLogout} type="button">
              {ru.signOut}
            </button>
          )}
        </div>
      </header>
      <div className="page-body">{children}</div>
    </>
  )

  if (standalone) {
    return (
      <main className="page">
        <div className="page-content shell">{content}</div>
      </main>
    )
  }

  return <div className="page-content">{content}</div>
}

export function Panel({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: ReactNode
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {hint && <p className="panel-hint">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty-state">{children}</p>
}

export function Badge({ active }: { active: boolean }) {
  return (
    <span className={`badge ${active ? 'badge-ok' : 'badge-muted'}`}>
      {active ? ru.active : ru.inactive}
    </span>
  )
}

export function Alert({ tone, children }: { tone: 'error' | 'success'; children: ReactNode }) {
  return <p className={tone === 'error' ? 'alert alert-error' : 'alert alert-success'}>{children}</p>
}
