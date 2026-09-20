import type { ReactNode } from 'react'

// Stable parents preserve the diagram portal, selected markers and live data.
export function TaskFirstRobotLayout({ identity, detail, taskFirst }: { identity: ReactNode; detail: ReactNode; taskFirst: boolean }) {
  return <div className={`rp-check-layout ${taskFirst ? 'a-robot-layout' : 'classic-robot-layout'}`} data-testid="robot-check-layout">
    <div className="rp-check-layout__overview" data-robot-overview>{identity}</div>
    <div className="rp-check-layout__details" data-robot-details>{detail}</div>
  </div>
}
