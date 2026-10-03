import type { ReactElement, ReactNode } from 'react'
import '../layout/MasterDetail.css'

export type EntityRowProps = {
  className?: string
  title: ReactNode
  meta?: ReactNode
  status?: ReactNode
  statusLabel?: string
  actions?: ReactNode
}

export function EntityRow({
  className,
  title,
  meta,
  status,
  statusLabel = 'Статус',
  actions,
}: EntityRowProps): ReactElement {
  return (
    <article className={`rp-entity-row${className ? ` ${className}` : ''}`}>
      <div className="rp-entity-row__content">
        <div className="rp-entity-row__title">{title}</div>
        {meta ? <div className="rp-entity-row__meta">{meta}</div> : null}
      </div>
      {status ? <div aria-label={statusLabel} className="rp-entity-row__status" role="group">{status}</div> : null}
      {actions ? <div className="rp-entity-row__actions">{actions}</div> : null}
    </article>
  )
}
