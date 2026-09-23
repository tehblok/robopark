import type { ReactNode } from 'react'

export function DomainPresentation({ route: _route, context, children }: { route: string; context?: ReactNode; children: ReactNode }) {
  return <div className="rp-domain-composition">
    {context ? <aside>{context}</aside> : null}
    <section className="rp-domain-workflow">{children}</section>
  </div>
}
