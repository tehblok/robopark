import type { ReactNode } from 'react'

export function TaskFirstLayout({ primary, context, actions }: { primary: ReactNode; context?: ReactNode; actions?: ReactNode }) {
  return <div className="a-layout">
    <section className="a-primary">{primary}</section>
    {context ? <aside className="a-context">{context}</aside> : null}
    {actions ? <div className="a-actions">{actions}</div> : null}
  </div>
}
