import { useId, type ReactElement, type ReactNode } from 'react'
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
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = '',
}: PanelProps): ReactElement {
  const headingId = useId()

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
          {actions ? <div className="rp-panel__actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="rp-panel__content">{children}</div>
    </section>
  )
}
