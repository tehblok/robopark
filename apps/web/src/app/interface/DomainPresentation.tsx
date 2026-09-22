import type { ReactNode } from 'react'
import { usePresentationMode } from './presentationModeContext'

export function DomainPresentation({ route, context, children }: { route: string; context?: ReactNode; children: ReactNode }) {
  const taskFirst = usePresentationMode() === 'task-first'
  return <div className={taskFirst ? 'rp-domain-composition a-domain-composition' : 'rp-domain-composition'} data-a-route={taskFirst ? route : undefined}>
    {context ? taskFirst
      ? <div data-a-zone="intro">{context}</div>
      : <aside>{context}</aside> : null}
    <section className="rp-domain-workflow" data-a-zone={taskFirst ? 'workflow' : undefined}>{children}</section>
  </div>
}
