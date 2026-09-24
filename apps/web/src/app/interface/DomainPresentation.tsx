import type { ReactNode } from 'react'

export function DomainPresentation({ route: _route, children }: { route: string; children: ReactNode }) {
  return <div className="rp-domain-composition">
    <section className="rp-domain-workflow">{children}</section>
  </div>
}
