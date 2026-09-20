import type { ReactNode } from 'react'
import { usePresentationMode } from './presentationModeContext'

export function DomainPresentation({ route, context, children }: { route: string; context?: ReactNode; children: ReactNode }) {
  const taskFirst = usePresentationMode() === 'task-first'
  return <div className={taskFirst ? 'a-domain-composition' : undefined} data-a-route={taskFirst ? route : undefined}>
    {context ? <aside data-a-zone={taskFirst ? 'context' : undefined}>{context}</aside> : null}
    <section data-a-zone={taskFirst ? 'workflow' : undefined}>{children}</section>
  </div>
}
