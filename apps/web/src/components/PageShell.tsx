import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ru } from '../i18n/ru'

type PageShellProps = {
  title: string
  subtitle?: string
  backTo?: string
  backLabel?: string
  onLogout?: () => void
  children: ReactNode
}

export function PageShell({
  title,
  subtitle,
  backTo,
  backLabel = ru.back,
  onLogout,
  children,
}: PageShellProps) {
  return (
    <main className="page">
      <div className="shell">
        <header className="shell-header">
          <div className="shell-brand">
            <span className="brand-mark">{ru.brand}</span>
            <div>
              <h1>{title}</h1>
              {subtitle && <p className="shell-subtitle">{subtitle}</p>}
            </div>
          </div>
          <div className="shell-actions">
            {backTo && (
              <Link className="btn btn-ghost" to={backTo}>{backLabel}</Link>
            )}
            {onLogout && (
              <button className="btn btn-secondary" onClick={onLogout} type="button">
                {ru.signOut}
              </button>
            )}
          </div>
        </header>
        {children}
      </div>
    </main>
  )
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
