import type { ReactNode } from 'react'
import { usePresentationMode } from './presentationModeContext'

export function DomainPresentation({ route, context, children }: { route: string; context?: ReactNode; children: ReactNode }) {
  const taskFirst = usePresentationMode() === 'task-first'
  if (!taskFirst) return <>{context}{children}</>
  return <div className="a-domain-composition" data-a-route={route}>
    {context ? <aside data-a-zone="context">{context}</aside> : null}
    <section data-a-zone="workflow">{children}</section>
  </div>
}
