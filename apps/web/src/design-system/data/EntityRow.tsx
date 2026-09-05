import type { ReactElement, ReactNode } from 'react'
import '../layout/MasterDetail.css'

export type EntityRowProps = {
  title: ReactNode
  meta?: ReactNode
  status?: ReactNode
  actions?: ReactNode
}

export function EntityRow({
  title,
  meta,
  status,
  actions,
}: EntityRowProps): ReactElement {
  return (
    <article className="rp-entity-row">
      <div className="rp-entity-row__content">
        <div className="rp-entity-row__title">{title}</div>
        {meta ? <div className="rp-entity-row__meta">{meta}</div> : null}
      </div>
      {status ? <div aria-label="Статус" className="rp-entity-row__status" role="group">{status}</div> : null}
      {actions ? <div className="rp-entity-row__actions">{actions}</div> : null}
    </article>
  )
}
