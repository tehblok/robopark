import { useId, useState, type ReactElement, type ReactNode } from 'react'
import './PageLayout.css'

export type PageLayoutProps = {
  title: ReactNode
  description?: ReactNode
  eyebrow?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function PageLayout({
  title,
  description,
  eyebrow,
  actions,
  children,
  className = '',
}: PageLayoutProps): ReactElement {
  return (
    <div className={`rp-page-layout ${className}`.trim()}>
      <header className="rp-page-layout__header">
        <div className="rp-page-layout__heading">
          {eyebrow ? <div className="rp-page-layout__eyebrow">{eyebrow}</div> : null}
          <h1 className="rp-page-layout__title">{title}</h1>
          {description ? <div className="rp-page-layout__description">{description}</div> : null}
        </div>
        {actions ? <div className="rp-page-layout__actions">{actions}</div> : null}
      </header>
      <div className="rp-page-layout__content">{children}</div>
    </div>
  )
}

export type PanelProps = {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  collapsible?: boolean
  storageKey?: string
  defaultCollapsed?: boolean
}

function panelStorageKey(storageKey: string): string {
  return `robopark:panel:${storageKey}:collapsed`
}

function readCollapsed(storageKey: string | undefined, fallback: boolean): boolean {
  if (!storageKey || typeof window === 'undefined') return fallback
  try {
    return window.localStorage.getItem(panelStorageKey(storageKey)) === '1'
  } catch {
    return fallback
  }
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = '',
  collapsible = false,
  storageKey,
  defaultCollapsed = false,
}: PanelProps): ReactElement {
  const headingId = useId()
  const contentId = useId()
  if (import.meta.env.DEV && collapsible && (typeof title !== 'string' || !title.trim() || !storageKey?.trim())) {
    throw new Error('A collapsible Panel requires a nonempty string title and storageKey.')
  }
  const [collapsed, setCollapsed] = useState(() => readCollapsed(storageKey, defaultCollapsed))
  const canCollapse = collapsible && typeof title === 'string' && Boolean(title.trim() && storageKey?.trim())
  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    try {
      if (storageKey) {
        if (next) window.localStorage.setItem(panelStorageKey(storageKey), '1')
        else window.localStorage.removeItem(panelStorageKey(storageKey))
      }
    } catch {
      // Persistent storage can be unavailable in private or embedded contexts.
    }
  }

  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={`rp-panel ${className}`.trim()}
    >
      {title || description || actions ? (
        <header className="rp-panel__header">
          <div className="rp-panel__heading">
            {title ? <h2 className="rp-panel__title" id={headingId}>{title}</h2> : null}
            {description ? <div className="rp-panel__description">{description}</div> : null}
          </div>
          {actions || canCollapse ? <div className="rp-panel__actions">
            {canCollapse ? <button
              aria-controls={contentId}
              aria-expanded={!collapsed}
              className="rp-panel__collapse"
              onClick={toggleCollapsed}
              type="button"
            >
              {collapsed ? `Развернуть: ${title}` : `Свернуть: ${title}`}
            </button> : null}
            {actions}
          </div> : null}
        </header>
      ) : null}
      {!collapsed ? <div className="rp-panel__content" id={contentId}>{children}</div> : null}
    </section>
  )
}
